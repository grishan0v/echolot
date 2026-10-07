#!/usr/bin/env python3
"""Draws the README's pictures from their sources.

    python docs/assets/render.py              write the README's sample report, then
                                              every picture next to this file
    python docs/assets/render.py --preview D  also write README-like pages into D:
                                              light and dark, desktop and phone

A picture makes claims the way the sentences around it do, so the numbers in
these come from where the text gets them: the trace, report and sample figures
are read out of README.md, the sample comparison out of docs/compare.md, and
the cost of a hunt is worked out from the recorded runs listed below. tests/test_readme_pictures.py draws everything
again and fails when a committed file differs, so a README edit that moves a
number fails until the pictures are drawn again.

The README's sample report is not written by hand either: it is what `analyze`
prints for the demo app (echolot/demo.py), and it is written first, because the
pictures read their numbers from it. tests/test_doc_samples.py renders it again
and fails when the README's copy differs.

The rules every picture follows are in docs/assets/README.md.
"""
from __future__ import annotations

import argparse
import html
import random
import re
import sys
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
README = HERE.parent.parent / "README.md"
AGENT = HERE.parent.parent / "echolot" / "claude" / "agents" / "perf-hunter.md"
COMPARE_DOC = HERE.parent / "compare.md"

SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', "
        "Helvetica, Arial, sans-serif")
MONO = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', monospace")

# GitHub's own greys, so a picture sits in the page, and the logo's palette for
# what carries meaning: deep teal is echolot, the warm end is where time went.
THEMES = {
    "light": dict(ink="#1F2328", sub="#59636E", faint="#818B98", line="#D1D9E0",
                  bar="#DCE1E6", card="#F6F8FA", teal="#2F5F73", teal_tint="#EEF3F5",
                  teal_line="#C9D9E0", on_teal="#FFFFFF", brick="#C9503A",
                  orange="#E07A45", sand="#E8C25B", hot_tint="#FAE7E2", page="#FFFFFF"),
    "dark": dict(ink="#F0F6FC", sub="#9198A1", faint="#656C76", line="#3D444D",
                 bar="#30363D", card="#151B23", teal="#6AA7BF", teal_tint="#122029",
                 teal_line="#29414D", on_teal="#0D1117", brick="#E26D55",
                 orange="#E8894F", sand="#E8C25B", hot_tint="#3A201B", page="#0D1117"),
}

# The cost of one hunt in US dollars, run by run: one cold-start bug in one
# Android app, one phone, the same task text, with echolot entered through
# /echolot and without it, August to September 2026. One of the Opus 5 runs
# with echolot ($6.42) is the one in which the agent went around the command;
# it stays in, since it happened.
HUNTS = {
    "Fable 5": {"alone": ["8.78", "9.87"], "echolot": ["3.80", "3.69", "3.33"]},
    "Opus 5": {"alone": ["6.81", "10.25", "10.28", "45.31"],
               "echolot": ["3.15", "3.82", "6.42"]},
    "Opus 4.8": {"alone": ["4.39", "4.79", "4.88"], "echolot": ["2.72", "2.87", "3.70"]},
    "Sonnet 5": {"alone": ["1.42", "1.52", "1.92"], "echolot": ["1.53", "1.83", "2.27"]},
}
HUNTS_MODEL = "Opus 5"          # the model the picture shows
HUNTS_VERSION = "0.5.3"         # the echolot the runs used

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}


# --- what the README says ------------------------------------------------------

def _find(pattern: str, text: str, what: str, flags: int = 0) -> re.Match:
    found = re.search(pattern, text, flags)
    if not found:
        raise SystemExit(f"README.md: {what} is gone or reworded, and a picture "
                         f"reads its numbers from it (/{pattern}/)")
    return found


def readme_facts(text: str | None = None) -> dict:
    text = README.read_text(encoding="utf-8") if text is None else text
    # Markdown rewraps freely, so any whitespace may stand between the words.
    trace = _find(r"An\s+(\d+)\s+MB\s+trace\s+with\s+(\d+)k\s+slices\s+comes\s+out\s+as\s+a\s+"
                  r"(\d+)\s+KB\s+`report\.json`\s+in\s+about\s+(\w+)\s+seconds", text,
                  "the sentence about the size of a trace and its report")
    sample = _find(r"^```markdown\n(# Marker Report\n.*?)^```", text,
                   "the sample Marker Report", re.S | re.M).group(1)
    window = _find(r"Scenario window: \*\*([\d.]+) ms\*\*", sample, "the sample's window")
    fired = _find(r"Detectors fired: \*\*(\d+) of (\d+)\*\*", sample, "the sample's tally")
    lock = _find(r"^\| [^|]+ \| \d+/\d+ \| \d+ \| ([\d.]+) \| [\d.]+ \| "
                 r"owner at (\w+)\.kt:(\d+)", sample, "the sample's monitor contention row",
                 re.M)
    repeats = _find(r"echolot collect -c echolot\.yml -n (\d+)", text,
                    "the collect example under Quick start")
    agent = AGENT.read_text(encoding="utf-8")
    rounds = re.search(r"`loop\.max_rounds` comes from the\s+config, default (\d+)", agent)
    if not rounds:
        raise SystemExit(f"{AGENT.name}: the sentence that gives loop.max_rounds its "
                         f"default is gone or reworded, and the loop picture reads it")
    seconds = trace.group(4)
    return {
        "repeats": int(repeats.group(1)),
        "max_rounds": int(rounds.group(1)),
        "trace_mb": int(trace.group(1)),
        "slices": int(trace.group(2)) * 1000,
        "report_kb": int(trace.group(3)),
        "seconds": NUMBER_WORDS.get(seconds, seconds),
        "window_ms": float(window.group(1)),
        "fired": int(fired.group(1)),
        "detectors": int(fired.group(2)),
        "lock_ms": lock.group(1),
        "lock_class": lock.group(2),
        "lock_line": int(lock.group(3)),
    }


def hunt_costs() -> dict:
    def cents(v: Decimal) -> str:
        return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))

    runs = HUNTS[HUNTS_MODEL]
    alone = [Decimal(v) for v in runs["alone"]]
    helped = [Decimal(v) for v in runs["echolot"]]
    cheaper = sum(1 for r in HUNTS.values()
                  if median(Decimal(v) for v in r["echolot"])
                  < median(Decimal(v) for v in r["alone"]))
    return {
        "alone": cents(median(alone)),
        "helped": cents(median(helped)),
        "worst": cents(max(alone)),
        "ratio": f"{median(alone) / median(helped):.1f}",
        "n_alone": len(alone),
        "n_helped": len(helped),
        "cheaper": cheaper,
        "models": len(HUNTS),
    }


# --- drawing -------------------------------------------------------------------

def text(x, y, s, *, size=16, weight=400, fill, family=SANS, anchor="start", cls=""):
    klass = f' class="{cls}"' if cls else ""
    return (f'<text{klass} x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
            f'xml:space="preserve" style="white-space:pre">{html.escape(s)}</text>')


def svg(w, h, body, title, style=""):
    head = f"<style>{style}</style>" if style else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(title)}">'
            f'<title>{html.escape(title)}</title>{head}{body}</svg>\n')


def arrow(x1, x2, y, color):
    return (f'<path d="M{x1} {y}H{x2 - 9}" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" fill="none"/>'
            f'<path d="M{x2 - 10} {y - 6}L{x2} {y}L{x2 - 10} {y + 6}Z" fill="{color}"/>')


def panel(x, y, w, h, *, fill, stroke, width=1.5, rx=10):
    """A card whose outer edge is exactly x, y, w, h: the stroke is drawn inside it,
    so a picture cropped to its cards starts at 0 like the text beside it."""
    s = width / 2
    return (f'<rect x="{x + s:g}" y="{y + s:g}" width="{w - width:g}" height="{h - width:g}" '
            f'rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{width:g}"/>')


def arrow_down(x, y1, y2, color):
    return (f'<path d="M{x} {y1}V{y2 - 9}" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" fill="none"/>'
            f'<path d="M{x - 6} {y2 - 10}L{x} {y2}L{x + 6} {y2 - 10}Z" fill="{color}"/>')


# Two sizes for every word in the hero, far enough apart to read as two levels:
# what a thing is, and the detail under it.
BIG, SMALL = 16, 13


def trace_panel(x, y, w, h, c):
    """The trace: a crowd of slices, a few of them hot."""
    out = [panel(x, y, w, h, fill=c["card"], stroke=c["line"])]
    rng = random.Random(7)
    row = y + 14
    while row + 7 <= y + h - 12:
        bx = x + 12 + rng.randint(0, 8)
        while True:
            bw = rng.choice([3, 4, 5, 6, 8, 10, 12, 16, 22, 30])
            if bx + bw > x + w - 12:
                break
            roll = rng.random()
            col = (c["brick"] if roll < 0.03 else c["orange"] if roll < 0.06
                   else c["sand"] if roll < 0.09 else c["bar"])
            out.append(f'<rect x="{bx}" y="{row}" width="{bw}" height="7" rx="1.5" fill="{col}"/>')
            bx += bw + rng.choice([2, 3, 3, 4, 6])
        row += 12
    return "".join(out)


def facts_panel(x, y, w, h, bars, c, f, *, first, step):
    """The report: rows of facts, one of them the lead. A 0 in `bars` is the lead,
    and it takes a little more room than a row, so it does not touch its neighbours."""
    out = [panel(x, y, w, h, fill=c["teal_tint"], stroke=c["teal"])]
    ry = y + first
    for length in bars:
        if length:
            out.append(f'<rect x="{x + 20}" y="{ry}" width="{length}" height="10" rx="5" '
                       f'fill="{c["teal_line"]}"/>')
            ry += step
            continue
        ry += 4
        out.append(f'<rect x="{x + 8}" y="{ry - 8}" width="{w - 16}" height="26" '
                   f'rx="6" fill="{c["teal"]}"/>')
        out.append(text(x + 16, ry + 10, f"lock wait · {f['lock_ms']} ms", size=SMALL,
                        weight=600, fill=c["on_teal"], family=MONO))
        ry += step + 4
    return "".join(out)


def code_panel(x, y, w, h, c, f):
    """The code: one line lit. The source is illustrative; the file and line are
    the sample report's."""
    out = [panel(x, y, w, h, fill=c["card"], stroke=c["line"]),
           text(x + 14, y + 24, f"{f['lock_class']}.kt", size=SMALL, fill=c["sub"], family=MONO),
           f'<path d="M{x + 1.5} {y + 36}H{x + w - 1.5}" stroke="{c["line"]}" stroke-width="1"/>']
    n = f["lock_line"]
    code = [(n - 2, "fun update(item: Item) {"), (n - 1, "  synchronized(lock) {"),
            (n, "    store.write(item)"), (n + 1, "  }"), (n + 2, "}")]
    ly = y + 62
    for num, src in code:
        if num == n:
            out.append(f'<rect x="{x + 1.5}" y="{ly - 16}" width="{w - 3}" height="24" '
                       f'fill="{c["hot_tint"]}"/>')
            out.append(f'<rect x="{x + 1.5}" y="{ly - 16}" width="3" height="24" '
                       f'fill="{c["brick"]}"/>')
        out.append(text(x + 14, ly, str(num), size=SMALL, fill=c["faint"], family=MONO))
        out.append(text(x + 38, ly, src, size=SMALL, fill=c["ink"], family=MONO,
                        weight=600 if num == n else 400))
        ly += 24
    out.append(text(x + 14, y + h - 16, f"main thread waits here · {f['lock_ms']} ms",
                    size=SMALL, weight=600, fill=c["brick"]))
    return "".join(out)


def caption(x, y, title, detail, c, *, mono=False):
    """What a thing is, and the detail under it."""
    return (text(x, y, title, size=BIG, weight=600, fill=c["ink"])
            + text(x, y + 20, detail, size=SMALL, fill=c["sub"], family=MONO if mono else SANS))


def hero_title(f):
    return f"From an {f['trace_mb']} MB trace to the line to fix"


def hero(c, f):
    """From a huge trace to the line to fix, in three panels side by side.

    No heading of its own: the line under the logo says what this shows. The
    panels span the whole width, from 0 to 880, so their edges are the edges
    of the text above and below."""
    ph = 196
    mid = ph / 2
    ax, aw = 0, 212
    bx, bw = 320, 196
    cx, cw = 624, 256
    out = [trace_panel(ax, 0, aw, ph, c),
           caption(ax, ph + 28, f"{f['trace_mb']} MB Perfetto trace", f"{f['slices']:,} slices", c),
           arrow(aw + 12, bx - 12, mid, c["teal"]),
           text((aw + bx) / 2, mid - 12, "echolot", size=BIG, weight=600, fill=c["teal"],
                anchor="middle"),
           text((aw + bx) / 2, mid + 24, f"about {f['seconds']} s", size=SMALL, fill=c["sub"],
                anchor="middle"),
           facts_panel(bx, 0, bw, ph, (110, 80, 128, 60, 0, 96, 70, 118), c, f,
                       first=16, step=21),
           caption(bx, ph + 28, "20 rows of facts", f"{f['report_kb']} KB, evidence included", c),
           arrow(bx + bw + 12, cx - 12, mid, c["ink"]),
           text((bx + bw + cx) / 2, mid - 12, "your agent", size=BIG, weight=600, fill=c["ink"],
                anchor="middle"),
           code_panel(cx, 0, cw, ph, c, f),
           caption(cx, ph + 28, "the line to fix", f"{f['lock_class']}.kt:{f['lock_line']}", c,
                   mono=True)]
    return svg(880, ph + 52, "".join(out), hero_title(f))


def follows_the_system(light: dict, dark: dict) -> str:
    """CSS that repaints a picture drawn in the light colours when the reader's
    system asks for dark. Each light colour is used for one role only, so the
    swap can go by the colour itself."""
    rules = [f'[fill="{v}"]{{fill:{dark[k]}}}[stroke="{v}"]{{stroke:{dark[k]}}}'
             for k, v in light.items() if dark[k] != v]
    return "@media (prefers-color-scheme:dark){" + "".join(rules) + "}"


def hero_narrow(f):
    """The hero for a phone: the same three panels, one under the other, at a
    width a phone shows close to its own size, so the words stay readable.

    GitHub picks a README picture by the width of the screen or by its own
    theme setting, but a <source> that asks for both is not safe there: GitHub
    rewrites the theme half of the condition. So this one comes as a single
    file that follows the reader's system theme on its own."""
    c = THEMES["light"]
    w, ph, gap = 360, 120, 52
    pw, tx = 180, 200            # the panels on the left, every word on one line at tx
    b_top = ph + gap
    c_top = b_top + ph + gap
    code_h = 196
    out = [trace_panel(0, 0, pw, ph, c),
           caption(tx, ph / 2 - 2, f"{f['trace_mb']} MB Perfetto trace", f"{f['slices']:,} slices", c),
           arrow_down(pw / 2, ph + 8, b_top - 8, c["teal"]),
           text(tx, ph + 22, "echolot", size=BIG, weight=600, fill=c["teal"]),
           text(tx, ph + 42, f"about {f['seconds']} s", size=SMALL, fill=c["sub"]),
           facts_panel(0, b_top, pw, ph, (104, 0, 90, 116), c, f, first=21, step=20),
           caption(tx, b_top + ph / 2 - 2, "20 rows of facts",
                   f"{f['report_kb']} KB, evidence included", c),
           arrow_down(pw / 2, b_top + ph + 8, c_top - 8, c["ink"]),
           text(tx, b_top + ph + gap / 2 + 5, "your agent", size=BIG, weight=600, fill=c["ink"]),
           code_panel(0, c_top, w, code_h, c, f),
           caption(0, c_top + code_h + 28, "the line to fix",
                   f"{f['lock_class']}.kt:{f['lock_line']}", c, mono=True)]
    return svg(w, c_top + code_h + 52, "".join(out), hero_title(f),
               style=follows_the_system(THEMES["light"], THEMES["dark"]))


def versus(c, f):
    h = hunt_costs()
    out = [text(40, 54, f"One bug, one model: {h['ratio']}× cheaper with echolot", size=30,
                weight=700, fill=c["ink"])]
    top, ch = 80, 224
    lx, rx = 40, 448
    out.append(f'<rect x="{lx}" y="{top}" width="392" height="{ch}" rx="12" '
               f'fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1"/>')
    out.append(text(lx + 24, top + 38, "Agent alone", size=18, weight=700, fill=c["sub"]))
    out.append(f'<path d="M{lx + 24} {top + 78}C{lx + 70} {top + 40} {lx + 90} {top + 110} '
               f'{lx + 130} {top + 70}S{lx + 170} {top + 50} {lx + 200} {top + 88}'
               f'S{lx + 250} {top + 110} {lx + 230} {top + 64}S{lx + 300} {top + 60} '
               f'{lx + 330} {top + 84}S{lx + 360} {top + 96} {lx + 368} {top + 74}" fill="none" '
               f'stroke="{c["faint"]}" stroke-width="2.25" stroke-linecap="round" '
               f'stroke-dasharray="1 6"/>')
    out.append(text(lx + 24, top + 158, f"${h['alone']}", size=44, weight=700, fill=c["ink"]))
    out.append(text(lx + 182, top + 158, "per hunt, median", size=15, fill=c["sub"]))
    out.append(text(lx + 24, top + 186, "a new approach every run", size=15, fill=c["sub"]))
    out.append(text(lx + 24, top + 208, f"one run cost ${h['worst']}", size=15, fill=c["sub"]))

    out.append(f'<rect x="{rx}" y="{top}" width="392" height="{ch}" rx="12" '
               f'fill="{c["teal_tint"]}" stroke="{c["teal"]}" stroke-width="2"/>')
    out.append(text(rx + 24, top + 38, "Agent + echolot", size=18, weight=700, fill=c["teal"]))
    out.append(f'<path d="M{rx + 24} {top + 76}H{rx + 350}" stroke="{c["teal"]}" '
               f'stroke-width="2.25" stroke-linecap="round"/><path d="M{rx + 346} {top + 70}'
               f'L{rx + 358} {top + 76}L{rx + 346} {top + 82}Z" fill="{c["teal"]}"/>')
    for i, label in enumerate(("record", "read 20 rows", "mark, repeat")):
        px = rx + 40 + i * 120
        out.append(f'<circle cx="{px}" cy="{top + 76}" r="6" fill="{c["teal"]}"/>')
        out.append(text(px, top + 100, label, size=13, fill=c["sub"], anchor="middle"))
    out.append(text(rx + 24, top + 158, f"${h['helped']}", size=44, weight=700,
                    fill=c["teal"]))
    out.append(text(rx + 158, top + 158, "per hunt, median", size=15, fill=c["sub"]))
    out.append(text(rx + 24, top + 186, "one fixed loop, round after round", size=15,
                    fill=c["sub"]))
    out.append(text(rx + 24, top + 208, "scripts measure, the model decides", size=15,
                    fill=c["sub"]))
    out.append(text(40, 336, f"{HUNTS_MODEL} on one cold-start bug in a real Android app: "
                    f"{h['n_alone']} runs alone, {h['n_helped']} with echolot {HUNTS_VERSION}. "
                    f"Cheaper on {h['cheaper']} of the {h['models']} models tested.",
                    size=13, fill=c["sub"]))
    return svg(880, 360, "".join(out),
               f"One bug, one model: {h['ratio']} times cheaper with echolot")


def connector(points, color):
    """A line through the points, with an arrowhead at the last one."""
    (x1, y1), (x2, y2) = points[-2], points[-1]
    length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    stops = [f"{x:g} {y:g}" for x, y in points[:-1]] + [f"{x2 - ux * 9:g} {y2 - uy * 9:g}"]
    bx, by, px, py = x2 - ux * 10, y2 - uy * 10, -uy * 6, ux * 6
    return (f'<path d="M{" L".join(stops)}" fill="none" stroke="{color}" stroke-width="1.75" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="M{x2:g} {y2:g}L{bx + px:g} {by + py:g}L{bx - px:g} {by - py:g}Z" '
            f'fill="{color}"/>')


def loop(c, f):
    """The hunt as echolot and the agent share it: scripts measure, the agent decides."""
    out = [text(40, 50, "echolot does the legwork, your agent decides", size=30, weight=700,
                fill=c["ink"])]
    ly = 86
    out.append(f'<rect x="40" y="{ly - 11}" width="22" height="14" rx="3" fill="{c["teal"]}"/>')
    out.append(text(70, ly, "echolot runs it, the same way every time", size=14, fill=c["sub"]))
    out.append(f'<rect x="370.75" y="{ly - 10.25}" width="20.5" height="12.5" rx="3" '
               f'fill="none" stroke="{c["ink"]}" stroke-width="1.5"/>')
    out.append(text(400, ly, "the agent decides", size=14, fill=c["sub"]))
    out.append(f'<rect x="550.75" y="{ly - 10.25}" width="20.5" height="12.5" rx="3" '
               f'fill="none" stroke="{c["sub"]}" stroke-width="1.5" stroke-dasharray="4 3"/>')
    out.append(text(580, ly, "you", size=14, fill=c["sub"]))

    w, h = 240, 104
    left, middle, right = 40, 320, 600
    top, mid, low = 120, 304, 488

    def box(x, y, kind, title, lines, mono=False):
        if kind == "echolot":
            shape = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" '
                     f'fill="{c["teal"]}"/>')
            ink = sub = c["on_teal"]
        else:
            fill = c["card"] if kind == "answer" else "none"
            edge = c["sub"] if kind == "you" else c["ink"]
            dash = ' stroke-dasharray="5 4"' if kind == "you" else ""
            shape = (f'<rect x="{x + 0.75}" y="{y + 0.75}" width="{w - 1.5}" height="{h - 1.5}" '
                     f'rx="10" fill="{fill}" stroke="{edge}" '
                     f'stroke-width="{2 if kind == "answer" else 1.5}"{dash}/>')
            ink, sub = c["ink"], c["sub"]
        parts = [shape, text(x + 18, y + 34, title, size=17, weight=700, fill=ink,
                             family=MONO if mono else SANS)]
        for i, line in enumerate(lines):
            parts.append(text(x + 18, y + 60 + i * 22, line, size=14, fill=sub))
        return "".join(parts)

    arrows = c["sub"]
    out.append(connector([(left + w, top + h / 2), (middle, top + h / 2)], arrows))
    out.append(connector([(middle + w, top + h / 2), (right, top + h / 2)], arrows))
    out.append(connector([(right + w / 2, top + h), (right + w / 2, mid)], arrows))
    out.append(connector([(right, mid + h / 2), (middle + w, mid + h / 2)], arrows))
    out.append(connector([(middle, mid + h / 2), (left + w, mid + h / 2)], arrows))
    out.append(connector([(middle + w / 2, mid + h), (middle + w / 2, low)], arrows))
    out.append(connector([(left + w / 2, mid), (left + w / 2, 264), (middle + w / 2, 264),
                          (middle + w / 2, top + h)], arrows))
    out.append(text(left + w / 2 + 16, 288, f"next round · up to {f['max_rounds']} by default",
                    size=14, fill=c["sub"]))
    out.append(text(300, mid + h / 2 - 10, "no", size=14, fill=c["sub"], anchor="middle"))
    out.append(text(middle + w / 2 + 12, 452, "yes", size=14, fill=c["sub"]))

    out.append(box(left, top, "you", "You", ["say what regressed", "and after which change"]))
    out.append(box(middle, top, "echolot", "collect",
                   [f"records the scenario {f['repeats']} times", "gradle mode: its own count"],
                   mono=True))
    out.append(box(right, top, "echolot", "analyze",
                   [f"runs the {f['detectors']} detectors", "writes about 20 rows"], mono=True))
    out.append(box(right, mid, "echolot", "compare",
                   ["from round 2: what moved", "and whether the move holds"], mono=True))
    out.append(box(middle, mid, "agent", "The agent", ["reads the rows:", "a place in the code?"]))
    out.append(box(left, mid, "agent", "The agent",
                   ["marks one blind spot", "with 5–7 temporary markers"]))
    out.append(box(middle, low, "answer", "The answer",
                   ["place, evidence, mechanism,", "confidence; markers removed"]))
    out.append(text(left, 520, "echolot mark --apply", size=13, weight=600, fill=c["ink"],
                    family=MONO))
    out.append(text(left, 542, "places the markers when the", size=14, fill=c["sub"]))
    out.append(text(left, 562, "project has none of its own", size=14, fill=c["sub"]))
    return svg(880, 616, "".join(out), "echolot does the legwork, your agent decides")


def compare_rows() -> list[dict]:
    """The sample comparison in docs/compare.md, row by row.

    That table is held to the columns `compare` prints by tests/test_docs.py,
    so the picture drawn from it shows what the tool shows.
    """
    text_ = COMPARE_DOC.read_text(encoding="utf-8")
    found = re.search(r"^\| Where \| Evidence \| Detector \| Before \| After \| Δ \| N \| Holds \|\n"
                      r"\|[-|]+\|\n((?:\|.*\|\n)+)", text_, re.M)
    if not found:
        raise SystemExit("docs/compare.md: the sample comparison is gone or its columns "
                         "changed, and the compare picture is drawn from it")

    def median_of(cell):
        return None if cell == "—" else cell.split()[0]

    rows = []
    for line in found.group(1).strip().splitlines():
        cells = [cell.strip().replace("**", "") for cell in line.strip().strip("|").split("|")]
        where, _evidence, detector, before, after, delta, calls, holds = cells
        n_before, n_after = (None if v.strip() == "—" else int(v) for v in calls.split("→"))
        verdict = None
        if holds != "—":
            m = re.match(r"(yes|no), ([+-]?[\d.]+) … ([+-]?[\d.]+)(?: · (.+))?$", holds)
            if not m:
                raise SystemExit(f"docs/compare.md: a Holds cell the picture cannot read: {holds!r}")
            verdict = {"holds": m.group(1) == "yes", "lo": float(m.group(2)),
                       "hi": float(m.group(3)), "note": m.group(4)}
        rows.append(dict(where=where, detector=detector, before=median_of(before),
                         after=median_of(after), delta=delta.split(), n_before=n_before,
                         n_after=n_after, verdict=verdict))
    return rows


def spans(x, y, parts, *, size, family=SANS):
    """One line of text in several colours: parts are (words, colour, weight)."""
    inner = "".join(f'<tspan fill="{colour}" font-weight="{weight}">{html.escape(words)}</tspan>'
                    for words, colour, weight in parts)
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'xml:space="preserve" style="white-space:pre">{inner}</text>')


def compare(c, f):
    """compare's sample, a card per finding: what moved, how often it ran, whether it holds."""
    rows = compare_rows()
    top, step = 112, 92
    out = [text(40, 50, "What changed, and whether it holds", size=30, weight=700,
                fill=c["ink"])]
    for x, label in ((60, "Where"), (300, "Before → After"), (452, "Δ"), (560, "N"),
                     (690, "Holds, 95% range")):
        out.append(text(x, 96, label, size=13, weight=600, fill=c["sub"]))
    for i, r in enumerate(rows):
        y0 = top + i * step
        out.append(f'<rect x="40" y="{y0}" width="800" height="80" rx="10" '
                   f'fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1"/>')
        out.append(text(60, y0 + 34, r["where"], size=15, weight=700, fill=c["ink"],
                        family=MONO))
        out.append(text(60, y0 + 58, r["detector"], size=12.5, fill=c["sub"], family=MONO))
        out.append(spans(300, y0 + 34, [(r["before"] or "—", c["sub"], 400),
                                        ("  →  ", c["faint"], 400),
                                        (r["after"] or "—", c["ink"], 700),
                                        (" ms" if r["after"] else "", c["sub"], 400)],
                         size=16))
        # A row found on one side only has a word for its Δ, `new` or `gone`.
        one_side = r["delta"][0] if r["delta"] in (["new"], ["gone"]) else None
        if one_side:
            width = {"new": 46, "gone": 54}[one_side]
            out.append(f'<rect x="452" y="{y0 + 17}" width="{width}" height="24" rx="6" '
                       f'fill="{c["hot_tint"]}"/>')
            out.append(text(452 + width // 2, y0 + 34, one_side, size=14, weight=700,
                            fill=c["brick"], anchor="middle"))
        else:
            grown, factor = r["delta"]
            out.append(text(452, y0 + 34, grown, size=16, weight=700, fill=c["brick"]))
            out.append(text(452, y0 + 58, factor, size=12.5, fill=c["sub"]))
        before, after = r["n_before"], r["n_after"]
        out.append(text(560, y0 + 34, f"{'—' if before is None else before} → "
                                      f"{'—' if after is None else after}",
                        size=15, fill=c["ink"]))
        if before is None:
            meaning = "no slices on it" if after == 0 else "new"
        elif after is None:
            meaning = "gone"
        elif after > before:
            meaning = "called more often"
        elif after < before:
            meaning = "called less often"
        else:
            meaning = "slower inside"
        out.append(text(560, y0 + 58, meaning, size=12.5, fill=c["sub"]))
        v = r["verdict"]
        if v is None:
            out.append(text(690, y0 + 34, "—", size=15, fill=c["faint"]))
            out.append(text(690, y0 + 58, "gone, no range" if one_side == "gone"
                            else "new, no range yet", size=12.5, fill=c["sub"]))
            continue
        lo, hi = min(v["lo"], 0.0), max(v["hi"], 0.0)
        pad = (hi - lo) * 0.08
        x0, x1 = 690, 824

        def at(value, lo=lo, hi=hi, pad=pad, x0=x0, x1=x1):
            return round(x0 + (value - lo + pad) / (hi - lo + 2 * pad) * (x1 - x0), 1)

        gy = y0 + 26
        out.append(f'<path d="M{x0} {gy}H{x1}" stroke="{c["line"]}" stroke-width="2" '
                   f'stroke-linecap="round"/>')
        out.append(f'<rect x="{at(v["lo"])}" y="{gy - 4}" width="{round(at(v["hi"]) - at(v["lo"]), 1)}" '
                   f'height="8" rx="4" fill="{c["teal"] if v["holds"] else c["faint"]}"/>')
        out.append(f'<path d="M{at(0)} {gy - 9}V{gy + 9}" stroke="{c["ink"]}" stroke-width="1.5"/>')
        out.append(spans(690, y0 + 52, [("yes" if v["holds"] else "no",
                                         c["teal"] if v["holds"] else c["sub"], 700),
                                        (f"  {v['lo']:+.1f} … {v['hi']:+.1f}".replace("-", "−"), c["sub"], 400)],
                         size=12.5))
        if v["note"]:
            out.append(text(690, y0 + 70, f"needs {v['note']}", size=12, fill=c["sub"]))
    foot = top + len(rows) * step + 16
    out.append(text(40, foot, "The sample from Comparing, as compare prints it: medians of the "
                    "runs on each side, sorted by how far each row moved.", size=13,
                    fill=c["sub"]))
    return svg(880, foot + 16, "".join(out), "What changed, and whether it holds")


# The session is one picture for both themes: a terminal keeps its own colours.
TERM = dict(bg="#10191D", fg="#E6E1D6", dim="#7A898F", edge="#33424A", sand="#E8C25B",
            green="#8DC9A0")
SESSION_SECONDS = 18.0


def session(f):
    """A condensed /echolot session, replayed as the protocol runs it.

    The session builds up in the first eight seconds and then stands whole for
    the next nine, so a reader who looks at it at any moment mostly finds it
    full, and one who arrives at the start sees lines within two seconds."""
    t = TERM
    total = SESSION_SECONDS
    command = "/echolot why is cold start slow"
    question = "After which change did it get slower?"
    char_w = BIG * 0.6            # a monospace advance
    lines = [  # (seconds, y, [(x, words, colour, weight)])
        (2.0, 116, [(28, "?", t["sand"], 700), (48, question, t["fg"], 400)]),
        (2.6, 116, [(48 + len(question) * char_w + 18, "since the tab redesign", t["green"], 400)]),
        (3.2, 148, [(28, "✓", t["green"], 700),
                    (48, f"hunt #1 opened · {f['repeats']} traces recorded", t["fg"], 400)]),
        (4.0, 188, [(28, "round 1", t["dim"], 400),
                    (128, f"analyze · {f['fired']} of {f['detectors']} detectors fired · "
                          f"window {round(f['window_ms']):,} ms", t["fg"], 400)]),
        (4.8, 220, [(28, "round 2", t["dim"], 400),
                    (128, "echolot mark --apply · 6 markers · recorded again · compare",
                     t["fg"], 400)]),
        (5.6, 252, [(28, "round 3", t["dim"], 400),
                     (128, f"markers around {f['lock_class']} · the move holds",
                      t["fg"], 400)]),
        (6.6, 300, [(28, "Place", t["dim"], 400),
                     (160, f"{f['lock_class']}.kt:{f['lock_line']}", t["green"], 700)]),
        (6.9, 330, [(28, "Evidence", t["dim"], 400),
                     (160, f"the main thread waits {f['lock_ms']} ms on a lock update() holds",
                      t["fg"], 400)]),
        (7.2, 360, [(28, "Suggestion", t["dim"], 400),
                     (160, "take store.write() out of the synchronized block", t["fg"], 400)]),
        (7.5, 390, [(28, "Confidence", t["dim"], 400), (160, "high", t["fg"], 400)]),
        (7.8, 420, [(28, "Cleanup", t["dim"], 400),
                     (160, "temporary markers removed", t["fg"], 400)]),
    ]
    def pct(seconds: float) -> str:
        return f"{seconds / total * 100:.2f}%"

    style = [".l{animation-duration:%gs;animation-timing-function:linear;"
             "animation-iteration-count:infinite;animation-fill-mode:both}" % total]
    body = [f'<rect x="0.5" y="0.5" width="879" height="447" rx="12" fill="{t["bg"]}" '
            f'stroke="{t["edge"]}"/>']
    for dx in (22, 40, 58):
        body.append(f'<circle cx="{dx}" cy="22" r="5" fill="{t["edge"]}"/>')
    body.append(text(440, 27, "claude — ~/my-app", size=SMALL, fill=t["dim"], family=MONO,
                     anchor="middle"))
    body.append(text(856, 27, "condensed replay", size=SMALL, fill=t["dim"], anchor="end"))
    body.append(f'<path d="M1 44H879" stroke="{t["edge"]}"/>')

    # the command, typed: a block the colour of the terminal slides off it
    typed_w = len(command) * char_w
    start, end = pct(0.2), pct(1.4)
    style.append("@keyframes type{0%%{transform:translateX(0)}%s{transform:translateX(0);"
                 "animation-timing-function:steps(%d,end)}%s,98%%{transform:translateX(%gpx)}"
                 "100%%{transform:translateX(0)}}" % (start, len(command), end, typed_w))
    style.append("@keyframes cursor{0%%,%s{opacity:1}%s,100%%{opacity:0}}"
                 % (pct(1.8), pct(1.9)))
    style.append(".type{transform:translateX(%gpx);animation:type %gs linear infinite both}"
                 % (typed_w, total))
    style.append(".cursor{animation:cursor %gs linear infinite both}" % total)
    body.append(text(28, 84, "›", size=BIG, weight=700, fill=t["sand"], family=MONO))
    body.append(text(48, 84, command, size=BIG, fill=t["fg"], family=MONO))
    body.append(f'<g class="type"><rect class="cursor" x="48" y="69" width="{char_w:g}" '
                f'height="19" fill="{t["sand"]}"/><rect x="{48 + char_w:g}" y="67" '
                f'width="{typed_w + 10:g}" height="24" fill="{t["bg"]}"/></g>')

    for i, (at, y, words) in enumerate(lines):
        name = f"a{i}"
        style.append("@keyframes %s{0%%,%s{opacity:0}%s,95%%{opacity:1}98%%,100%%{opacity:0}}"
                     % (name, pct(at), pct(at + 0.25)))
        style.append(f".{name}{{animation-name:{name}}}")
        for x, s, colour, weight in words:
            body.append(text(round(x, 1), y, s, size=BIG, weight=weight, fill=colour,
                             family=MONO, cls=f"l {name}"))
    style.append("@media (prefers-reduced-motion:reduce){.l,.type,.cursor{animation:none}"
                 ".type{display:none}}")
    return svg(880, 448, "".join(body),
               "A condensed /echolot session: the question, three rounds, the answer",
               style="".join(style))


THEMED = {"hero": hero, "versus": versus, "loop": loop, "compare": compare}


def pictures(readme: str | None = None) -> dict[str, str]:
    """Every picture this script draws, by file name."""
    facts = readme_facts(readme)
    out = {}
    for name, draw in THEMED.items():
        for theme, colours in THEMES.items():
            out[f"{name}-{theme}.svg"] = draw(colours, facts)
    out["hero-narrow.svg"] = hero_narrow(facts)
    out["session.svg"] = session(facts)
    return out


def preview_page(theme: str, assets: Path, width: int = 928) -> str:
    """A page shaped like the top of the README on GitHub, for looking at both
    themes and at a phone's width. It picks each picture the way the README's
    <picture> elements do: the narrow hero below 768 pixels, the theme's own
    file above."""
    c = THEMES[theme]
    phone = width < 768
    hero_file = "hero-narrow.svg" if phone else f"hero-{theme}.svg"

    def img(name, w=880):
        return f'<p><img src="{assets / name}" width="{w}"></p>'

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="color-scheme" content="{theme}"><style>
body{{margin:0;background:{c['page']};color:{c['ink']};font-family:{SANS};font-size:16px;line-height:1.5}}
.box{{max-width:{width}px;margin:{0 if phone else 24}px auto;border:1px solid {c['line']};border-radius:6px}}
.md{{padding:{16 if phone else 24}px}} img{{max-width:100%}} p,ul{{margin:0 0 16px}}
.c{{text-align:center}} a{{color:{c['teal']}}}
h2{{font-size:24px;border-bottom:1px solid {c['line']};padding-bottom:8px;margin:24px 0 16px}}
pre{{background:{c['card']};border-radius:6px;padding:12px 16px;margin:0 0 12px;font-family:{MONO};font-size:14px}}
</style></head><body><div class="box"><div class="md">
<p class="c"><img src="{assets / f'logo-{theme}.png'}" width="429"></p>
<p class="c"><b>Turns a huge Android trace into 20 rows of facts an AI agent can actually use.</b></p>
<p class="c">[ checks ] [ PyPI ] [ license ]</p>
<p><img src="{assets / hero_file}"></p>
<ul><li><b>Finds the line to fix.</b> From a slow screen to a file and line, with the numbers that prove it.</li>
<li><b>Same answer, every run.</b> A pinned trace_processor: the same trace always gives the same report.</li>
<li><b>No tracing code needed.</b> Works on apps with zero trace {{}} calls; it places temporary markers itself.</li>
<li><b>Works with your agent.</b> A plugin for Claude Code and Codex; Cursor and others via <code>echolot guide</code>.</li></ul>
{img('session.svg')}
<p><b>Contents</b> · <a>Quick start</a> · <a>How it works</a> · <a>What it saves</a> · …</p>
<p><b>Reference</b> · <a>Detectors</a> · <a>Commands</a> · <a>Requirements</a> · …</p>
<h2>Quick start</h2><pre>pipx install echolot</pre>
<pre>claude plugin marketplace add grishan0v/echolot &amp;&amp; claude plugin install echolot@echolot   # Claude Code
codex plugin marketplace add grishan0v/echolot &amp;&amp; codex plugin add echolot@echolot         # Codex</pre>
<pre>/echolot</pre>
<h2>How it works</h2>{img(f'loop-{theme}.svg')}
<h2>What it saves</h2>{img(f'versus-{theme}.svg')}
<h2>What changed</h2>{img(f'compare-{theme}.svg')}
</div></div></body></html>"""


SAMPLE = re.compile(r"^```markdown\n# Marker Report\n.*?^```", re.S | re.M)


def write_sample() -> bool:
    """The README's sample report, rendered from the demo app. True when it changed."""
    sys.path.insert(0, str(README.parent))
    from echolot import demo, recorder
    from echolot import report as report_mod

    with recorder.isolated():
        rendered = report_mod.to_markdown(demo.report()).rstrip("\n")
    text = README.read_text(encoding="utf-8")
    if not SAMPLE.search(text):
        raise SystemExit("README.md: the ```markdown block holding the sample report is gone")
    new = SAMPLE.sub(lambda _: f"```markdown\n{rendered}\n```", text, count=1)
    if new == text:
        return False
    README.write_text(new, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="render.py", description="Draws the README's pictures from their sources.")
    parser.add_argument("--preview", metavar="DIR", type=Path,
                        help="also write README-like pages into DIR: light and dark, "
                             "desktop and phone")
    args = parser.parse_args(argv)
    if write_sample():
        print(f"→ {README} (the sample report)")
    for name, body in pictures().items():
        (HERE / name).write_text(body, encoding="utf-8")
        print(f"→ {HERE / name}")
    if args.preview is not None:
        target = args.preview
        target.mkdir(parents=True, exist_ok=True)
        for theme in THEMES:
            for kind, width in (("", 928), ("-phone", 375)):
                page = target / f"preview-{theme}{kind}.html"
                page.write_text(preview_page(theme, HERE, width), encoding="utf-8")
                print(f"→ {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
