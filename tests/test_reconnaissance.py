#!/usr/bin/env python3
"""`probe`, `names` and `explain` — the three the agent reads before deciding.

They had no tests anywhere. Not in `doctor`, which pins the detectors and the
config, and not here: a coverage sweep across the three layers found `probe`
and `explain` at zero in all of them, and `names` at two mentions.

That is a gap of a particular kind. These commands do not compute a verdict, so
nothing downstream goes red when they drift — they just quietly start saying
something else, and what reads them is an agent deciding where to look. A probe
that lists the wrong process, or an anchor candidate list that stops being
sorted by duration, sends the whole hunt somewhere else and reports nothing.

They run against the same synthetic trace `doctor` uses, whose contents are
known exactly, so what they must say is a fact rather than a shape.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

# What the fixture plants, and therefore what these must find. Named here so a
# fixture change that moves them fails with the number rather than the shape.
APP = "com.example.app"
APP_PID = 4100
OTHER = "com.other.app"
BLIND_THREAD = "DefaultDispatcher-worker-1"      # 300 ms Running, no slices
START_ANCHOR = "AppStart"                        # 1006 ms, the longest slice


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("recon") / "fixture.perfetto-trace"
    p.write_bytes(fixture.build())
    return p


def run(*argv: str) -> str:
    """A command, with what it printed. Exit code checked; stderr is noise."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(argv))
    check(f"`{' '.join(argv[:2])}` exits 0", code == 0, f"exit {code}")
    return out.getvalue()


# --- probe ------------------------------------------------------------------

def test_probe_names_the_processes_largest_first(trace):
    """The first thing an agent reads, and what `project.process` is set from."""
    text = run("probe", str(trace))
    check("the app is listed", APP in text, text[:300])
    check("with its pid", str(APP_PID) in text, text[:300])
    check("and so is the foreign process", OTHER in text, text[:300])
    check("the app comes first — it has the most slices",
          text.index(APP) < text.index(OTHER), text[:300])


def test_probe_breaks_a_named_process_down_by_thread(trace):
    text = run("probe", str(trace), "--process", APP)
    check("the section is there", "Threads of process" in text, text[:200])
    check("the blind-spot thread is listed", BLIND_THREAD in text, text)

    # Threads by CPU, busiest first: a thread with 300 ms Running and no
    # slices at all is the one worth instrumenting, and sorting by slice
    # count would bury it at the bottom.
    # That section only — the next one is a different table with different
    # columns, and reading past the boundary picks its rows up as if they
    # were threads.
    body = text.split("Threads of process", 1)[1].split("\n## ", 1)[0]
    rows = [r for r in body.splitlines() if r.startswith("| ") and "---" not in r]
    running = [float(r.split("|")[5]) for r in rows[1:]]
    check("some threads came back", running, body[:200])
    check("sorted by time on CPU, descending",
          running == sorted(running, reverse=True), running)
    check("and the blind-spot thread is above the busier-by-slices ones",
          running[1] == 300.0, running)


def test_probe_offers_the_longest_slices_as_anchor_candidates(trace):
    """`scenario.start` is chosen from this list, so its order is the advice."""
    text = run("probe", str(trace), "--process", APP)
    section = text.split("anchor candidates", 1)[-1]
    check("the longest slice is the first candidate",
          section.index(START_ANCHOR) < section.index("blocking_io_wait"),
          section[:300])


def test_probe_refuses_a_mask_that_matches_nothing(trace):
    """It used to print an empty table and exit 0.

    Everything else in this tool shouts when the ground it checked was empty —
    an anchor that never matched, a detector the config left out. An agent that
    mistypes a process name got `_empty_` and a clean exit, which reads as "no
    threads" rather than "no such process".
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["probe", str(trace), "--process", "com.nothing.here"])
    check("it refuses", code == 2, f"exit {code}")
    check("naming the mask", "com.nothing.here" in err.getvalue(), err.getvalue())
    check("and the process table is still printed, to choose from",
          APP in out.getvalue(), out.getvalue()[:200])


# --- names ------------------------------------------------------------------

def test_names_collapses_families_and_shows_what_the_masks_see(trace):
    """From a trace to "which detector will see this name", before running any."""
    text = run("names", str(trace), "--process", APP, "--min-ms", "0", "--top", "200")
    check("GC shows up", "GC" in text, text[:400])
    check("and the lock family too",
          "Lock contention" in text, text[:400])

    # The owner's tid lives inside the name, so an inventory that did not
    # collapse digits would run to a row per owner.
    check("digits collapsed into a family",
          "#" in text, text[:400])


def test_names_reports_what_no_mask_covers(trace):
    """The section that says where a detector would be blind."""
    text = run("names", str(trace), "--process", APP, "--min-ms", "0", "--top", "200")
    check("the missed section exists", "Missed by the masks" in text, text[-800:])


# --- explain ----------------------------------------------------------------

def test_explain_lists_every_shipped_detector_with_its_parameters():
    """The agent's way to ask what can fire, without opening any .sql."""
    from echolot.main import DETECTOR_DIR
    from echolot.tp import load_detectors

    text = run("explain")
    shipped = load_detectors(DETECTOR_DIR)
    check("something is shipped", shipped, "no detectors found")
    for d in shipped:
        check(f"`{d.id}` is listed", d.id in text, text[:200])
        for param in d.params:
            check(f"with its parameter {d.id}.{param}", param in text, text[:400])


def test_explain_needs_no_trace_and_no_config():
    """It reads the .sql files and nothing else, which is why it always works."""
    text = run("explain")
    check("a title came through", "why:" in text, text[:200])
