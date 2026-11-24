#!/usr/bin/env python3
"""`echolot guide report` is a page, and nothing it leaves out is lost (#854).

The report reference was one file of 77 KB, printed whole by `guide report`,
and perf-hunter was told to look one field up there: one field cost the agent
every other (#493). It is now a page of 8 KB at most on how to read a report,
and each part it leaves out is a topic of its own, `guide report-<topic>`,
which the page names. What one detector means comes from `echolot explain`,
which prints that detector's notes from the topic on the detectors.

The doctor checks "guide: `guide report` is a page of 8 KB at most…" and
"explain: a detector says how to read its rows…" hold the same.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import explain as explain_mod  # noqa: E402
from echolot import layer  # noqa: E402
from echolot.analyze import DETECTOR_DIR  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.tp import load_detectors  # noqa: E402

SHIPPED = load_detectors(DETECTOR_DIR)
TOPICS = layer.guide_topics()
PARTS = sorted(t for t in TOPICS if t.startswith(layer.REPORT_TOPIC_PREFIX))
NOTES = layer.REFERENCES_DIR / explain_mod.NOTES


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


def _flat(text: str) -> str:
    return " ".join(text.split())


# --- the page ---------------------------------------------------------------

def test_guide_report_prints_8_kb_at_most():
    code, out = run("guide", "report")
    size = len(out.encode("utf-8"))
    check("exit 0", code == 0, code)
    check(f"{size} bytes, at most {layer.GUIDE_REPORT_LIMIT}",
          size <= layer.GUIDE_REPORT_LIMIT, size)


def test_the_page_says_the_reading_order_the_verdict_and_the_four_states():
    """What #493 kept on the page: the shape, the verdict, `runs`,
    `unfinished`, `checked` — and where to go next."""
    page = _flat(layer.guide_text(TOPICS["report"]))
    for said in ("echolot report -d <id>", "echolot report --window",
                 "echolot report --markers", "`needs_attention`", "`worth_a_look`",
                 "`checked: false`", "`unfinished: true`", "`runs`",
                 "An empty report", "echolot explain <id>"):
        check(f"the page says {said}", said in page, page[:300])


def test_the_report_reference_is_cut_into_topics():
    check("there are topics", len(PARTS) >= 8, PARTS)
    for t in ("report-schema", "report-window", "report-environment", "report-markers",
              "report-checks", "report-columns", "report-detectors", "report-compare"):
        check(f"{t} is a topic", t in TOPICS, sorted(TOPICS))


@pytest.mark.parametrize("topic", PARTS)
def test_every_topic_is_named_by_the_page_and_prints_its_file(topic):
    page = layer.guide_text(TOPICS["report"])
    check(f"the page sends the reader to `echolot guide {topic}`",
          f"`echolot guide {topic}`" in page, page[-1500:])
    code, out = run("guide", topic)
    check("exit 0", code == 0, code)
    check(f"`guide {topic}` prints its file", out.strip() == layer.guide_text(TOPICS[topic]),
          out[:200])
    check("under a title of its own", out.startswith("# "), out[:80])
    check("and lives beside the page", TOPICS[topic].parent == layer.REFERENCES_DIR,
          TOPICS[topic])


def test_the_schema_says_which_topic_has_every_key_it_leaves_out():
    schema = _flat(layer.guide_text(TOPICS["report-schema"]))
    for t in PARTS:
        if t in ("report-schema", "report-detectors", "report-compare"):
            continue
        check(f"the schema sends a key to {t}", f"echolot guide {t}" in schema, schema[:400])


# --- one detector -----------------------------------------------------------

def test_each_heading_in_the_notes_is_a_shipped_detector_and_the_table_lists_all():
    explain_mod._all_notes.cache_clear()
    notes = explain_mod._all_notes()
    ids = {d.id for d in SHIPPED}
    check("no notes under a name that is no detector", set(notes) <= ids,
          sorted(set(notes) - ids))
    check("the detectors the page explained at length still have notes",
          {"frame_jank", "io_wait", "uninstrumented_cpu", "main_thread_outlier"} <= set(notes),
          sorted(notes))
    listed = re.findall(r"^\| `([a-z0-9_]+)` \|", NOTES.read_text(encoding="utf-8"), re.M)
    check("the table lists every shipped detector, and nothing else",
          sorted(listed) == sorted(ids), (sorted(ids - set(listed)), sorted(set(listed) - ids)))


@pytest.mark.parametrize("d", SHIPPED, ids=lambda d: d.id)
def test_explain_prints_the_detectors_notes_after_its_columns(d):
    said = explain_mod.notes(d.id)
    code, out = run("explain", d.id)
    check("exit 0", code == 0, code)
    if said is None:
        check("no notes, no heading for them", "Reading its rows" not in out, out[-300:])
        return
    columns, notes = out.find("Columns, a row per"), out.find("Reading its rows")
    check("the notes come after the columns", 0 <= columns < notes, (columns, notes))
    for block in said.split("\n\n"):
        rows = block.split("\n")
        if all(r.startswith("    ") for r in rows):
            check("an example line as the report prints it", all(r in out for r in rows), block)
        else:
            check("each paragraph on a line, for a grep", _flat(block) in out, block[:120])


def test_explain_json_carries_the_notes():
    code, out = run("explain", "frame_jank", "--json")
    d = json.loads(out)["detectors"][0]
    check("exit 0", code == 0, code)
    check("notes, as markdown", d["notes"] and "Buffer Stuffing" in d["notes"], d.get("notes"))


def test_a_terminal_gets_the_notes_wrapped_and_the_example_kept_whole():
    e = explain_mod.entries([d for d in SHIPPED if d.id == "uninstrumented_cpu"], None)[0]
    wrapped = explain_mod.one(e, None, None, width=60)
    after = wrapped[wrapped.index("Reading its rows"):].splitlines()[1:]
    prose = [ln for ln in after if ln and not ln.startswith("    ")]
    check("prose wrapped to the width", prose and all(len(ln) <= 60 for ln in prose), after)
    check("the example line kept whole",
          any(ln.startswith("    100.0% of CPU outside slices") for ln in after), after)


# --- who is sent where ------------------------------------------------------

def test_perf_hunter_looks_one_detector_up_in_explain_and_never_reads_the_guide_whole():
    loop = _flat(layer.HUNTER.read_text(encoding="utf-8"))
    check("no longer: the guide is its schema and every detector at length",
          "is its schema and every detector at length" not in loop, loop)
    check("one detector is explain, how to read its rows included",
          "how to read its rows is `echolot explain <detector>`" in loop, loop)
    check("a field is one topic, never the guide whole",
          "Read that one topic, never the guide whole" in loop, loop)
    check("and the topic named is one the guide prints",
          "`echolot guide report-schema`" in loop and "report-schema" in TOPICS, loop)


def test_the_skill_names_the_page_and_its_topics():
    skill = _flat((layer.REFERENCES_DIR.parent / "SKILL.md").read_text(encoding="utf-8"))
    check("the page", "`references/report.md`" in skill, skill)
    check("its topics", "`references/report-schema.md`" in skill, skill)
    check("and one detector", "`echolot explain <id>`" in skill, skill)
