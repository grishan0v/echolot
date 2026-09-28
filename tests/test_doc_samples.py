#!/usr/bin/env python3
"""The sample Marker Report in the README, held to what the renderer prints.

The sample is the first report most people see, and it went stale the way
prose does. A `Runs` column arrived, then a Markers table, then an "In the
code" column, and the sample kept the shape it was written in: sections in
an order the report never used, italic lines that were no detector's `@why`,
a Silent list one detector short of the twelve it claimed. `test_docs.py`
checked the one number in it and nothing around the number.

The numbers stay free — they are illustrative, and a sample that had to match
the fixture's figures would stop reading like an app. What is checked is the
shape, against the fixture's own report merged the way `analyze` merges
repeats and rendered by the same function: the header's lines, the Markers
table, every section's heading, italic line, table header and footer, the
order the sections come in, and the Silent line.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import report as report_mod  # noqa: E402
from echolot.main import DETECTOR_DIR  # noqa: E402
from echolot.tp import load_detectors  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The column a row grows when the checkout holds the file it names. The
# fixture is analysed with no checkout behind it, so its tables never have
# one; the sample shows it on purpose, and it is left out of the comparison.
PLACED = "In the code"


def _sample() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    found = re.search(r"^```markdown\n(# Marker Report\n.*?)^```", text, re.S | re.M)
    assert found, "README.md: no ```markdown block starting with `# Marker Report`"
    return found.group(1)


def _rendered(marker_report: dict) -> str:
    """The fixture's report as three repeats merged, and a config behind it."""
    merged = report_mod.aggregate([copy.deepcopy(marker_report) for _ in range(3)])
    merged["config"] = {"path": "/project/echolot.yml", "sha": "0" * 12,
                        "local": None, "defaults": False, "set": None}
    return report_mod.to_markdown(merged)


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


def test_the_readme_sample_has_the_shape_analyze_prints(marker_report):
    sample, rendered = _cut(_sample()), _cut(_rendered(marker_report))

    check("the header has the lines analyze writes, in its order",
          sample["labels"] == rendered["labels"],
          f"README {sample['labels']}\nrenderer {rendered['labels']}")

    by_heading = {s["heading"]: s for s in rendered["sections"]}
    order = [s["heading"] for s in rendered["sections"]]
    shown = [s["heading"] for s in sample["sections"]]
    check("every section in the sample is one the renderer prints",
          set(shown) <= set(by_heading),
          f"not printed by any detector: {sorted(set(shown) - set(by_heading))}")
    check("and they come in the renderer's order — the markers, then the "
          "detectors in file order",
          shown == [h for h in order if h in shown],
          f"README {shown}\nrenderer {order}")

    for section in sample["sections"]:
        want = by_heading.get(section["heading"])
        if want is None:
            continue
        name = section["heading"]
        check(f"{name}: the italic line is the renderer's",
              section["italic"] == want["italic"],
              f"README {section['italic']!r}\nrenderer {want['italic']!r}")
        check(f"{name}: the table has the renderer's columns",
              [c for c in section["header"] if c != PLACED] == want["header"],
              f"README {section['header']}\nrenderer {want['header']}")
        check(f"{name}: every row has a cell per column",
              all(len(row) == len(section["header"]) for row in section["rows"]),
              str(section["rows"]))
        check(f"{name}: the footer names the detector and its shipped thresholds",
              section["footer"] == want["footer"],
              f"README {section['footer']!r}\nrenderer {want['footer']!r}")


def test_the_readme_sample_accounts_for_every_detector(marker_report):
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

    rendered = _cut(_rendered(marker_report))
    check("and the last line is the pinned trace_processor",
          sample["last"] == rendered["last"],
          f"README {sample['last']!r}\nrenderer {rendered['last']!r}")
