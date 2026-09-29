#!/usr/bin/env python3
"""Draws the README's pictures from their sources.

    python docs/assets/render.py              write every picture next to this file
    python docs/assets/render.py --preview D  also write two README-like pages into D

A picture makes claims the way the sentences around it do, so the numbers in
these come from where the text gets them: the trace, report and sample figures
are read out of README.md, and the cost of a hunt is worked out from the
recorded runs listed below. tests/test_readme_pictures.py draws everything
again and fails when a committed file differs, so a README edit that moves a
number fails until the pictures are drawn again.

The rules every picture follows are in docs/assets/README.md.
"""
from __future__ import annotations

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


def hero(c, f):
    """From a huge trace to the line to fix, in three panels."""
    out = [text(40, 62, f"From an {f['trace_mb']} MB trace to the line to fix", size=34,
                weight=700, fill=c["ink"]),
           text(40, 96, "Ask your AI agent in plain words. echolot gives it the facts "
                "to answer with.", size=18, fill=c["sub"])]
    top, ph = 128, 196
    mid = top + ph / 2

    # the trace: a crowd of slices, a few of them hot
    ax, aw = 40, 196
    out.append(f'<rect x="{ax}" y="{top}" width="{aw}" height="{ph}" rx="10" '
               f'fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1.5"/>')
    rng = random.Random(7)
    y = top + 14
    for _ in range(14):
        x = ax + 12 + rng.randint(0, 8)
        while True:
            bw = rng.choice([3, 4, 5, 6, 8, 10, 12, 16, 22, 30])
            if x + bw > ax + aw - 12:
                break
            roll = rng.random()
            col = (c["brick"] if roll < 0.03 else c["orange"] if roll < 0.06
                   else c["sand"] if roll < 0.09 else c["bar"])
            out.append(f'<rect x="{x}" y="{y}" width="{bw}" height="7" rx="1.5" fill="{col}"/>')
            x += bw + rng.choice([2, 3, 3, 4, 6])
        y += 12
    out.append(text(ax, top + ph + 28, f"{f['trace_mb']} MB Perfetto trace", size=16,
                    weight=600, fill=c["ink"]))
    out.append(text(ax, top + ph + 50, f"{f['slices']:,} slices", size=14, fill=c["sub"]))

    out.append(arrow(248, 316, mid, c["teal"]))
    out.append(text(282, mid - 12, "echolot", size=15, weight=700, fill=c["teal"],
                    anchor="middle"))
    out.append(text(282, mid + 24, f"about {f['seconds']} s", size=13, fill=c["sub"],
                    anchor="middle"))

    # the report: twenty rows, one of them the lead
    bx, bw_ = 328, 176
    out.append(f'<rect x="{bx}" y="{top}" width="{bw_}" height="{ph}" rx="10" '
               f'fill="{c["teal_tint"]}" stroke="{c["teal"]}" stroke-width="1.5"/>')
    ry = top + 20
    for length in (110, 80, 128, 60, 0, 96, 70, 118):
        if length:
            out.append(f'<rect x="{bx + 20}" y="{ry}" width="{length}" height="10" rx="5" '
                       f'fill="{c["teal_line"]}"/>')
        else:
            out.append(f'<rect x="{bx + 10}" y="{ry - 8}" width="{bw_ - 20}" height="26" '
                       f'rx="6" fill="{c["teal"]}"/>')
            out.append(text(bx + 18, ry + 10, f"lock wait · {f['lock_ms']} ms", size=12,
                            weight=600, fill=c["on_teal"], family=MONO))
        ry += 21
    out.append(text(bx, top + ph + 28, "20 rows of facts", size=16, weight=600, fill=c["ink"]))
    out.append(text(bx, top + ph + 50, f"{f['report_kb']} KB, evidence included", size=14,
                    fill=c["sub"]))

    out.append(arrow(516, 584, mid, c["ink"]))
    out.append(text(550, mid - 12, "your agent", size=14, weight=700, fill=c["ink"],
                    anchor="middle"))

    # the code: one line lit. The source is illustrative; the file and line
    # are the sample report's.
    cx, cw = 596, 244
    out.append(f'<rect x="{cx}" y="{top}" width="{cw}" height="{ph}" rx="10" '
               f'fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1.5"/>')
    out.append(text(cx + 14, top + 24, f"{f['lock_class']}.kt", size=12, fill=c["sub"],
                    family=MONO))
    out.append(f'<path d="M{cx} {top + 36}H{cx + cw}" stroke="{c["line"]}" stroke-width="1"/>')
    n = f["lock_line"]
    code = [(n - 2, "fun update(item: Item) {"), (n - 1, "  synchronized(lock) {"),
            (n, "    store.write(item)"), (n + 1, "  }"), (n + 2, "}")]
    ly = top + 62
    for num, src in code:
        if num == n:
            out.append(f'<rect x="{cx + 1}" y="{ly - 16}" width="{cw - 2}" height="24" '
                       f'fill="{c["hot_tint"]}"/>')
            out.append(f'<rect x="{cx + 1}" y="{ly - 16}" width="3" height="24" '
                       f'fill="{c["brick"]}"/>')
        out.append(text(cx + 14, ly, str(num), size=13, fill=c["faint"], family=MONO))
        out.append(text(cx + 38, ly, src, size=13, fill=c["ink"], family=MONO,
                        weight=600 if num == n else 400))
        ly += 24
    out.append(text(cx + 14, top + ph - 16, f"main thread waits here · {f['lock_ms']} ms",
                    size=13, weight=600, fill=c["brick"]))
    out.append(text(cx, top + ph + 28, "the line to fix", size=16, weight=600, fill=c["ink"]))
    out.append(text(cx, top + ph + 50, f"{f['lock_class']}.kt:{n}", size=14, fill=c["sub"],
                    family=MONO))
    return svg(880, 400, "".join(out),
               f"From an {f['trace_mb']} MB trace to the line to fix")


def icon_target(x, y, c):
    return (f'<g fill="none" stroke="{c["teal"]}" stroke-width="2.25" stroke-linecap="round">'
            f'<circle cx="{x + 16}" cy="{y + 16}" r="11"/>'
            f'<circle cx="{x + 16}" cy="{y + 16}" r="4"/>'
            f'<path d="M{x + 16} {y}V{y + 6}M{x + 16} {y + 26}V{y + 32}M{x} {y + 16}H{x + 6}'
            f'M{x + 26} {y + 16}H{x + 32}"/></g>')


def icon_same(x, y, c):
    return (f'<g fill="none" stroke="{c["teal"]}" stroke-width="2.25" stroke-linecap="round">'
            f'<circle cx="{x + 16}" cy="{y + 16}" r="14"/>'
            f'<path d="M{x + 9} {y + 12}H{x + 23}M{x + 9} {y + 20}H{x + 23}"/></g>')


def icon_nocode(x, y, c):
    brace = ("M{a} {t}Q{b} {t} {b} {u}V{v}Q{b} {m} {e} {m}Q{b} {m} {b} {w}V{z}"
             "Q{b} {s} {a} {s}")
    left = brace.format(a=x + 11, b=x + 5, e=x + 1, t=y + 4, u=y + 10, v=y + 13, m=y + 16,
                        w=y + 19, z=y + 22, s=y + 28)
    right = brace.format(a=x + 21, b=x + 27, e=x + 31, t=y + 4, u=y + 10, v=y + 13, m=y + 16,
                         w=y + 19, z=y + 22, s=y + 28)
    return (f'<g fill="none" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="{left}" stroke="{c["teal"]}"/><path d="{right}" stroke="{c["teal"]}"/>'
            f'<path d="M{x + 4} {y + 30}L{x + 28} {y + 2}" stroke="{c["brick"]}"/></g>')


def icon_agent(x, y, c):
    return (f'<g fill="none" stroke="{c["teal"]}" stroke-width="2.25" stroke-linejoin="round" '
            f'stroke-linecap="round"><path d="M{x + 3} {y + 6}Q{x + 3} {y + 2} {x + 7} {y + 2}'
            f'H{x + 25}Q{x + 29} {y + 2} {x + 29} {y + 6}V{y + 20}Q{x + 29} {y + 24} {x + 25} '
            f'{y + 24}H{x + 13}L{x + 7} {y + 30}V{y + 24}Q{x + 3} {y + 24} {x + 3} {y + 20}Z"/>'
            f'<path d="M{x + 10} {y + 13}H{x + 22}"/></g>')


BENEFITS = [
    (icon_target, "Finds the line to fix",
     ("From a slow screen to a file and line,", "with the numbers that prove it.")),
    (icon_same, "Same answer, every run",
     ("A pinned trace_processor: the same", "trace always gives the same report.")),
    (icon_nocode, "No tracing code needed",
     ("Works on apps with zero trace{} calls;", "it places temporary markers itself.")),
    (icon_agent, "Works with your agent",
     ("Claude Code out of the box; Cursor,", "Codex and others via echolot guide.")),
]


def benefits(c, f):
    out = []
    for i, (icon, head, body) in enumerate(BENEFITS):
        tx, ty = 40 + (i % 2) * 408, 24 + (i // 2) * 128
        out.append(f'<rect x="{tx}" y="{ty}" width="392" height="112" rx="12" '
                   f'fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1"/>')
        out.append(icon(tx + 20, ty + 22, c))
        out.append(text(tx + 72, ty + 42, head, size=20, weight=700, fill=c["ink"]))
        out.append(text(tx + 72, ty + 70, body[0], size=15, fill=c["sub"]))
        out.append(text(tx + 72, ty + 92, body[1], size=15, fill=c["sub"]))
    return svg(880, 288, "".join(out),
               ". ".join(head for _, head, _ in BENEFITS) + ".")


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


# The session is one picture for both themes: a terminal keeps its own colours.
TERM = dict(bg="#10191D", fg="#E6E1D6", dim="#7A898F", edge="#33424A", sand="#E8C25B",
            green="#8DC9A0")
SESSION_SECONDS = 18.0


def session(f):
    """A condensed /echolot session, replayed as the protocol runs it."""
    t = TERM
    total = SESSION_SECONDS
    command = "/echolot why is cold start slow"
    question = "After which change did it get slower?"
    char_w = 9.0
    lines = [  # (seconds, y, [(x, words, colour, weight)])
        (3.0, 116, [(28, "?", t["sand"], 700), (48, question, t["fg"], 400)]),
        (4.0, 116, [(48 + len(question) * char_w + 18, "since the tab redesign", t["green"], 400)]),
        (5.2, 148, [(28, "✓", t["green"], 700),
                    (48, "hunt #1 opened · 5 traces recorded", t["fg"], 400)]),
        (6.8, 188, [(28, "round 1", t["dim"], 400),
                    (128, f"analyze · {f['fired']} of {f['detectors']} detectors fired · "
                          f"window {round(f['window_ms']):,} ms", t["fg"], 400)]),
        (8.6, 220, [(28, "round 2", t["dim"], 400),
                    (128, "echolot mark --apply · 6 markers · recorded again · compare",
                     t["fg"], 400)]),
        (10.4, 252, [(28, "round 3", t["dim"], 400),
                     (128, f"markers around {f['lock_class']} · the move holds",
                      t["fg"], 400)]),
        (12.2, 300, [(28, "Place", t["dim"], 400),
                     (160, f"{f['lock_class']}.kt:{f['lock_line']}", t["green"], 700)]),
        (12.6, 330, [(28, "Evidence", t["dim"], 400),
                     (160, f"the main thread waits {f['lock_ms']} ms on a lock update() holds",
                      t["fg"], 400)]),
        (13.0, 360, [(28, "Suggestion", t["dim"], 400),
                     (160, "take store.write() out of the synchronized block", t["fg"], 400)]),
        (13.4, 390, [(28, "Confidence", t["dim"], 400), (160, "high", t["fg"], 400)]),
        (13.8, 420, [(28, "Cleanup", t["dim"], 400),
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
    body.append(text(440, 27, "claude — ~/my-app", size=13, fill=t["dim"], family=MONO,
                     anchor="middle"))
    body.append(text(856, 27, "condensed replay", size=12, fill=t["dim"], anchor="end"))
    body.append(f'<path d="M1 44H879" stroke="{t["edge"]}"/>')

    # the command, typed: a block the colour of the terminal slides off it
    typed_w = len(command) * char_w
    start, end = pct(0.3), pct(2.3)
    style.append("@keyframes type{0%%{transform:translateX(0)}%s{transform:translateX(0);"
                 "animation-timing-function:steps(%d,end)}%s,98%%{transform:translateX(%gpx)}"
                 "100%%{transform:translateX(0)}}" % (start, len(command), end, typed_w))
    style.append("@keyframes cursor{0%%,%s{opacity:1}%s,100%%{opacity:0}}"
                 % (pct(2.9), pct(3.0)))
    style.append(".type{transform:translateX(%gpx);animation:type %gs linear infinite both}"
                 % (typed_w, total))
    style.append(".cursor{animation:cursor %gs linear infinite both}" % total)
    body.append(text(28, 84, "›", size=15, weight=700, fill=t["sand"], family=MONO))
    body.append(text(48, 84, command, size=15, fill=t["fg"], family=MONO))
    body.append(f'<g class="type"><rect class="cursor" x="48" y="70" width="9" height="18" '
                f'fill="{t["sand"]}"/><rect x="57" y="68" width="{typed_w + 10}" height="22" '
                f'fill="{t["bg"]}"/></g>')

    for i, (at, y, words) in enumerate(lines):
        name = f"a{i}"
        style.append("@keyframes %s{0%%,%s{opacity:0}%s,95%%{opacity:1}98%%,100%%{opacity:0}}"
                     % (name, pct(at), pct(at + 0.25)))
        style.append(f".{name}{{animation-name:{name}}}")
        for x, s, colour, weight in words:
            body.append(text(round(x, 1), y, s, size=15, weight=weight, fill=colour,
                             family=MONO, cls=f"l {name}"))
    style.append("@media (prefers-reduced-motion:reduce){.l,.type,.cursor{animation:none}"
                 ".type{display:none}}")
    return svg(880, 448, "".join(body),
               "A condensed /echolot session: the question, three rounds, the answer",
               style="".join(style))


THEMED = {"hero": hero, "benefits": benefits, "versus": versus, "loop": loop}


def pictures(readme: str | None = None) -> dict[str, str]:
    """Every picture this script draws, by file name."""
    facts = readme_facts(readme)
    out = {}
    for name, draw in THEMED.items():
        for theme, colours in THEMES.items():
            out[f"{name}-{theme}.svg"] = draw(colours, facts)
    out["session.svg"] = session(facts)
    return out


LOGO = "https://github.com/user-attachments/assets/1cea634c-f0cd-4b32-a32f-c221e2be8227"


def preview_page(theme: str, assets: Path) -> str:
    """A page shaped like the top of the README on GitHub, for looking at both themes."""
    c = THEMES[theme]
    code_bg = c["card"]

    def themed(name):
        return f'<p align="center"><img src="{assets / f"{name}-{theme}.svg"}" width="880"></p>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:{c['page']};color:{c['ink']};font-family:{SANS};font-size:16px;line-height:1.5}}
.box{{width:928px;margin:24px auto;border:1px solid {c['line']};border-radius:6px}}
.md{{padding:24px}} img{{max-width:100%}} p{{margin:0 0 16px}}
h2{{font-size:24px;border-bottom:1px solid {c['line']};padding-bottom:8px;margin:24px 0 16px}}
pre{{background:{code_bg};border-radius:6px;padding:12px 16px;margin:0 0 12px;font-family:{MONO};font-size:14px}}
</style></head><body><div class="box"><div class="md">
<p align="center"><img src="{LOGO}" width="429"></p>
<p align="center"><b>Turns a huge Android trace into 20 rows of facts an AI agent can actually use.</b></p>
{themed('hero')}{themed('benefits')}
<p align="center"><img src="{assets / 'session.svg'}" width="880"></p>
<h2>Quick start</h2><pre>pipx install echolot</pre><pre>cd ~/my-app &amp;&amp; echolot init</pre><pre>/echolot</pre>
<h2>What it saves</h2>{themed('versus')}
<h2>How it works</h2>{themed('loop')}
</div></div></body></html>"""


def main(argv: list[str]) -> int:
    for name, body in pictures().items():
        (HERE / name).write_text(body, encoding="utf-8")
        print(f"→ {HERE / name}")
    if argv[:1] == ["--preview"] and len(argv) == 2:
        target = Path(argv[1])
        target.mkdir(parents=True, exist_ok=True)
        for theme in THEMES:
            page = target / f"preview-{theme}.html"
            page.write_text(preview_page(theme, HERE), encoding="utf-8")
            print(f"→ {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
