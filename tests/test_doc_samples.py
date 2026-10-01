#!/usr/bin/env python3
"""The sample Marker Report in the README, held to what `analyze` prints for the demo app.

The sample is the first report most people see, and it went stale the way
prose does. A `Runs` column arrived, then a Markers table, then an "In the
code" column, and the sample kept the shape it was written in: sections in
an order the report never used, italic lines that were no detector's `@why`,
a Silent list one detector short of the twelve it claimed. Then it was held
to the renderer's shape with its numbers left free, and its numbers drifted
apart from each other instead: the main thread waited 8% of its window for a
CPU, which is `runnable_starvation`'s finding, and the sample listed that
detector as silent.

So the sample is no longer written. It is the report of the demo app,
echolot/demo.py — a synthetic cold start built to read like an app —
numbers and all: `python docs/assets/render.py` writes it into the README,
and this test renders it again and compares the whole block.
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import report as report_mod  # noqa: E402
from echolot.main import DETECTOR_DIR  # noqa: E402
from echolot.tp import load_detectors  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _sample() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    found = re.search(r"^```markdown\n(# Marker Report\n.*?)^```", text, re.S | re.M)
    assert found, "README.md: no ```markdown block starting with `# Marker Report`"
    return found.group(1)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _cut(text: str) -> dict:
    """A report cut where the renderer cuts it: header, sections, tail."""
    lines = text.rstrip("\n").splitlines()
    assert lines and lines[0] == "# Marker Report", lines[:1]
    header: list[str] = []
    sections: list[dict] = []
    for line in lines[1:]:
        if line.startswith("## "):
            sections.append({"heading": line, "lines": []})
        elif sections:
            sections[-1]["lines"].append(line)
        else:
            header.append(line)

    for section in sections:
        body = section["lines"]
        section["italic"] = body[0] if body and body[0].startswith("_") else None
        tables = [line for line in body if line.startswith("|")]
        section["header"] = _cells(tables[0]) if tables else []
        section["rows"] = [_cells(line) for line in tables[2:]]
        section["footer"] = next((line for line in body
                                  if line.startswith("<sub>detector ")), None)
    return {
        # What the header says, by label: the numbers after it are the run's.
        "labels": [line.split(":", 1)[0] for line in header
                   if line.strip() and not line.startswith(">")],
        "fired": next((line for line in header if line.startswith("Detectors fired:")), ""),
        "sections": sections,
        "silent": next((line for line in lines if line.startswith("**Silent:**")), None),
        "last": lines[-1],
    }


def test_the_readme_sample_is_what_analyze_prints_for_the_demo(demo_report):
    rendered = report_mod.to_markdown(demo_report).rstrip("\n")
    sample = _sample().rstrip("\n")
    diff = "\n".join(list(difflib.unified_diff(
        sample.splitlines(), rendered.splitlines(), "README.md", "analyze", lineterm=""))[:40])
    check("the README's sample is the demo's report: `python docs/assets/render.py` "
          "writes it again", sample == rendered, diff)


def test_the_readme_sample_accounts_for_every_detector():
    """Fired and silent together are the whole set, in the order it is read.

    The sample said `5 of 12` with eleven names on the page between its
    sections and its Silent line. The count was checked; what it counted
    was not.
    """
    sample = _cut(_sample())
    detectors = load_detectors(DETECTOR_DIR)
    by_title = {d.title: d.id for d in detectors}
    fired = [by_title[s["heading"][3:]] for s in sample["sections"]
             if s["heading"][3:] in by_title]

    stated = re.search(r"\*\*(\d+) of \d+\*\*", sample["fired"])
    check("the header counts the sections below it",
          stated is not None and int(stated.group(1)) == len(fired),
          f"{sample['fired']!r} over {len(fired)} section(s)")

    silent = [d.id for d in detectors if d.id not in fired]
    check("the Silent line names every other detector, in file order",
          sample["silent"] == "**Silent:** " + ", ".join(silent),
          f"README {sample['silent']!r}\nexpected **Silent:** {', '.join(silent)}")

