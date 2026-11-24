"""`probe`'s start candidates: what a journey a command drives begins with.

A tap, an intent, a cold start: the platform's own sections, a few
milliseconds long, that never reached the 25 longest slices. Setup had
nothing to offer for a start, and the window opened at the process's first
slice, up to 614 ms before the tap (#615). doctor holds the candidates to
the window `analyze` opens with each; these hold the section probe prints.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import string
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_probe_process import build as only_the_apps_own  # noqa: E402

TITLE = "## Start candidates"


@pytest.fixture(scope="module")
def journey(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("journey") / "journey.perfetto-trace"
    path.write_bytes(fixture.journey())
    return path


def probe(*argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(["probe", *argv])
    check("exit 0", code == 0, f"exit {code}")
    return out.getvalue()


def section(text: str) -> str:
    check("the section is there", TITLE in text, text[-600:])
    return text.split(TITLE, 1)[1].split("\n## ", 1)[0]


def rows(body: str) -> list[list[str]]:
    lines = [ln for ln in body.splitlines() if ln.startswith("| ") and "---" not in ln]
    return [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[1:]]


def test_the_tap_comes_first_as_a_glob_with_its_time(journey):
    body = section(probe(str(journey), "--process", "com.example.app*"))
    got = rows(body)
    check("the app's platform events, earliest first",
          [r[0] for r in got] == ["deliverInputEvent*", "activityPause",
                                  "activityStart", "activityResume"], body)
    start, event, thread, at, n = got[0]
    check("the tap, at the time its span was queued", at == "650.0" and n == "4", got[0])
    check("said to be input", "tap" in event, event)
    check("the span is on no thread, and says so", thread == "(async)", thread)
    check("the others are the main thread's", {r[2] for r in got[1:]} == {"main"}, got)


def test_the_runner_the_mask_also_matches_offers_nothing(journey):
    """It binds an application and starts an activity before the app's first
    slice; under a mask that matches both, `bindApplication` led the list."""
    body = section(probe(str(journey), "--process", "com.example.app*"))
    check("no cold start in a recording of a running app", "bindApplication" not in body, body)


def test_it_says_how_to_write_one_and_where_the_window_opens_without(journey):
    body = section(probe(str(journey), "--process", "com.example.app"))
    flat = " ".join(body.split())
    check("the column is what `start:` takes", "Write the first column as `start:`" in flat, flat)
    check("the window opens at the first one", "at its first occurrence" in flat, flat)
    check("and with no start, at the app's first slice",
          "Without a `start:`, the window opens at the process's first slice, at 40.0 ms." in flat,
          flat)


def test_a_process_with_none_of_them_says_where_to_look(tmp_path):
    path = tmp_path / "plain.perfetto-trace"
    path.write_bytes(only_the_apps_own())
    body = section(probe(str(path), "--process", "com.example.app"))
    flat = " ".join(body.split())
    check("none, said", "None of the events a journey begins with" in flat, flat)
    check("with the categories they need", "`am` and `view`" in flat, flat)
    check("and how to find the app's own", "echolot names <trace> --grep" in flat, flat)
    check("no empty table", "| start |" not in body, body)


def test_the_main_fixture_offers_its_cold_start(tmp_path):
    """The cold start `doctor` reads: the process starts, then the activity."""
    path = tmp_path / "fixture.perfetto-trace"
    path.write_bytes(fixture.build())
    got = rows(section(probe(str(path), "--process", "com.example.app")))
    check("bindApplication first", [r[0] for r in got][:2] == ["bindApplication", "activityStart"],
          got)


def test_short_ones_are_found_among_hundreds_of_longer_sections(tmp_path):
    """On an app that writes hundreds of sections of its own, a tap and a
    pause made `names` only with `--top 223`: shorter than every one of them.
    The candidates are looked up by name, so their length is no bar."""
    letters = ("".join(p) for p in itertools.product(string.ascii_lowercase, repeat=2))
    own = [(f"section_{next(letters)}", 3 * i, 2, []) for i in range(300)]
    starts = [("deliverInputEvent src=0x1002 eventTimeNano=1 id=0x1", 905, 0.02, []),
              ("activityPause", 906, 0.01, [])]
    path = tmp_path / "busy.perfetto-trace"
    path.write_bytes(only_the_apps_own(app_slices=own + starts))
    got = rows(section(probe(str(path), "--process", "com.example.app")))
    check("both, the shortest slices of the process, earliest first",
          [(r[0], r[3]) for r in got] == [("deliverInputEvent*", "905.0"),
                                          ("activityPause", "906.0")], got)
