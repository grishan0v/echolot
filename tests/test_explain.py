#!/usr/bin/env python3
"""`echolot explain <detector>`: one detector in words, with its values here (#494).

Three things are held, none of which needs trace_processor. The header says,
for every shipped detector, what each threshold means and what a number is
counted in, and what each column of its rows means: a test over the files,
since that is where the words live. The header is read the way it is written,
a mask with its spaces and an old file with no words included. And the
command prints those words with the value `analyze` would use here, from the
config or a scenario's own section, with where it came from.

That the columns a header explains are the ones its query returns is held by
`doctor`, which has a trace every detector finds something in.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import explain as explain_mod  # noqa: E402
from echolot.main import DETECTOR_DIR, main  # noqa: E402
from echolot.tp import load_detectors, parse_header  # noqa: E402

SHIPPED = load_detectors(DETECTOR_DIR)


def run(*argv: str) -> tuple[int, str, str]:
    """A command: its exit code, and what it printed to stdout and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A folder to run in, with no echolot.yml until a test writes one."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def config(project: Path, text: str) -> Path:
    path = project / "echolot.yml"
    path.write_text("project:\n  package: com.example.app\n" + text, encoding="utf-8")
    return path


# --- the words, in every shipped header -------------------------------------

@pytest.mark.parametrize("d", SHIPPED, ids=lambda d: d.id)
def test_every_threshold_says_what_it_means_and_a_number_its_unit(d):
    for name, default in d.params.items():
        check(f"{d.id}.{name} says what it means", d.meanings.get(name), d.meanings)
        if isinstance(default, (int, float)):
            check(f"{d.id}.{name} says what it is counted in: `= {default} ms — …`",
                  d.units.get(name), d.units)


@pytest.mark.parametrize("d", SHIPPED, ids=lambda d: d.id)
def test_every_detector_explains_its_columns(d):
    """Which columns, against the query, is doctor's to say; that there are
    words for the ones that name a row is said here."""
    for name in ("location", "count", *d.identity):
        check(f"{d.id} explains `{name}`", name in d.columns, list(d.columns))
    for name, words in d.columns.items():
        check(f"{d.id}.{name} has words", words, d.columns)


# --- reading the header -----------------------------------------------------

HEADER = """\
-- @id: d
-- @why: w
-- @param: min_overrun_ms = 4 ms — how far past its deadline a frame
--       has to finish
-- @param: name_glob = binder transaction* — GLOB over slice names
-- @param: dash_glob = *-binder-* — a hyphen is no dash
-- @param: max_spread = 1.5 × — the longest over the shortest
-- @param: old = 50
-- @param: wrapped = 3 frames —
--       the words on the line below
-- @column: max_ms — the worst frame's
--       overrun
-- @column: location — why it missed
--
-- notes after the blank line are not a column's
"""


def test_a_number_is_followed_by_its_unit_and_the_words_by_a_dash():
    h = parse_header(HEADER)
    check("the number is a number", h.params["min_overrun_ms"] == 4, h.params)
    check("its unit", h.units["min_overrun_ms"] == "ms", h.units)
    check("its words, the line below joined on",
          h.meanings["min_overrun_ms"] == "how far past its deadline a frame has to finish",
          h.meanings)
    check("a float and a unit that is a sign", (h.params["max_spread"], h.units["max_spread"])
          == (1.5, "×"), h.params)
    check("the words may start on the line below",
          h.meanings["wrapped"] == "the words on the line below", h.meanings)


def test_a_mask_keeps_its_spaces_and_its_hyphens():
    h = parse_header(HEADER)
    check("the mask is everything before the dash",
          h.params["name_glob"] == "binder transaction*", h.params)
    check("a string has no unit", h.units["name_glob"] == "", h.units)
    check("a hyphen inside a mask is the mask's", h.params["dash_glob"] == "*-binder-*",
          h.params)


def test_a_header_with_no_words_still_reads():
    """A detector a project dropped in, written before #494: it runs as it did,
    and only a shipped one is held to the words."""
    h = parse_header(HEADER)
    check("the value", h.params["old"] == 50, h.params)
    check("no unit, no words", (h.units["old"], h.meanings["old"]) == ("", ""), h.meanings)


def test_columns_are_read_in_order_and_a_blank_line_ends_them():
    h = parse_header(HEADER)
    check("in the header's order", list(h.columns) == ["max_ms", "location"], h.columns)
    check("a column's words go on below", h.columns["max_ms"] == "the worst frame's overrun",
          h.columns)
    check("the notes after a blank `--` are no column's",
          "notes" not in h.columns["location"], h.columns)


@pytest.mark.parametrize("line", ["-- @column: max_ms — one\n-- @column: max_ms — two",
                                  "-- @column: — nothing named",
                                  "-- @column: two words — no"])
def test_a_column_said_twice_or_without_a_name_is_refused(line):
    with pytest.raises(ValueError, match="@column"):
        parse_header("-- @id: d\n" + line + "\n")


# --- the command ------------------------------------------------------------

def test_one_detector_names_its_thresholds_units_and_columns(project):
    code, out, _ = run("explain", "frame_jank")
    check("exit 0", code == 0, code)
    d = next(d for d in SHIPPED if d.id == "frame_jank")
    for name, default in d.params.items():
        check(f"{name} with its value and unit",
              f"  {name} = {default} {d.units[name]}" in out, out)
        check(f"and what {name} means", d.meanings[name] in out, out)
    for name, words in d.columns.items():
        check(f"the column {name}, with its words", f"  {name}" in out and words in out, out)
    check("max_ms is the overrun, not the frame", "not its duration" in out, out)
    check("what the trace has to hold", "Needs frame_timeline" in out, out)
    check("what calibrate derives a threshold from", "calibrate: top10(count) * 1.5" in out,
          out)


def test_a_name_that_is_no_detector_exits_2_with_the_nearest_and_the_list(project):
    code, out, err = run("explain", "frame_jnk")
    check("exit 2", code == 2, code)
    check("nothing on stdout", out == "", out)
    check("the nearest name", "Did you mean frame_jank?" in err, err)
    for d in SHIPPED:
        check(f"and the list, {d.id} in it", d.id in err, err)
    _, _, err = run("explain", "main_thread")
    check("a piece of a name offers every detector it is in",
          all(f"main_thread_{x}" in err.splitlines()[0]
              for x in ("block", "waits", "outlier", "kernel_waits")), err)


def test_a_threshold_the_config_sets_is_the_value_explain_prints(project):
    config(project, "detectors:\n  frame_jank:\n    min_frames: 7\n")
    code, out, _ = run("explain", "frame_jank", "-c", "echolot.yml")
    check("exit 0", code == 0, code)
    check("the config's value, where it came from and the default",
          "min_frames = 7 frames · from the config, default 3 frames" in out, out)
    check("the other is the default", "min_overrun_ms = 4 ms · default" in out, out)
    check("and the heading says whose values", "as echolot.yml runs them" in out, out)

    _, out, _ = run("explain", "frame_jank", "--json")
    p = {p["name"]: p for p in json.loads(out)["detectors"][0]["params"]}
    check("--json: the value, its source, the default and the unit",
          (p["min_frames"]["value"], p["min_frames"]["source"], p["min_frames"]["default"],
           p["min_frames"]["unit"]) == (7, "config", 3, "frames"), p["min_frames"])
    check("--json: the default's source", p["min_overrun_ms"]["source"] == "default", p)


def test_a_scenario_threshold_is_the_scenario_s(project):
    config(project, "detectors:\n  frame_jank:\n    min_frames: 7\n"
                    "scenarios:\n  cold:\n    window: launch\n"
                    "  scroll:\n    start: 'Scroll*'\n"
                    "    detectors:\n      frame_jank:\n        min_overrun_ms: 8.5\n")
    _, out, err = run("explain", "frame_jank", "--scenario", "scroll", "--json")
    doc = json.loads(out)
    p = {p["name"]: p for p in doc["detectors"][0]["params"]}
    check("which scenario", doc["scenario"] == "scroll", doc)
    check("its own threshold is the scenario's",
          (p["min_overrun_ms"]["value"], p["min_overrun_ms"]["source"]) == (8.5, "scenario"), p)
    check("the top's stays the config's", p["min_frames"]["source"] == "config", p)

    _, out, err = run("explain", "frame_jank")
    check("without --scenario, the first, said on stderr", "scenario cold" in err, err)
    check("where the scroll's threshold is not in force", "min_overrun_ms = 4 ms" in out, out)

    code, _, err = run("explain", "frame_jank", "--scenario", "nope")
    check("a scenario the config does not have: exit 2", code == 2, code)
    check("with the ones it has", "cold" in err and "scroll" in err, err)


def test_a_detector_the_config_turns_off_says_so(project):
    config(project, "detectors:\n  gc_pressure: false\n  frame_jank:\n    min_frames: 7\n")
    _, out, _ = run("explain")
    line = {ln.split(" ", 1)[0]: ln for ln in out.splitlines() if ln}
    check("off, on its line", line["gc_pressure"].endswith("· off in this config"), out)
    check("tuned, on its line", line["frame_jank"].endswith("· tuned: min_frames = 7 frames"),
          out)
    check("the rest as they ship", "·" not in line["io_wait"], out)
    _, out, _ = run("explain", "gc_pressure")
    check("and on its own page", "Turned off in this config" in out, out)
    _, out, _ = run("explain", "--json")
    off = {d["id"]: d["off"] for d in json.loads(out)["detectors"]}
    check("--json: off", off["gc_pressure"] is True and off["io_wait"] is False, off)


def test_a_config_analyze_would_refuse_is_refused(project):
    """Shown as the defaults instead, it would be the numbers this command
    exists to stop an agent guessing."""
    config(project, "detectors:\n  frame_jank:\n    min_frames: 7ms\n")
    code, out, err = run("explain", "frame_jank")
    check("exit 2", code == 2, code)
    check("nothing on stdout", out == "", out)
    check("the reason analyze gives", "must be a number" in err, err)


def test_a_config_named_and_not_there_exits_2(project):
    code, _, err = run("explain", "-c", "nope.yml")
    check("a named config that is not there", code == 2 and "config not found" in err, err)
    code, _, err = run("explain", "--scenario", "cold")
    check("a scenario with no config to name it", code == 2 and "--scenario" in err, err)


def test_json_lists_every_detector_with_its_words(project):
    code, out, _ = run("explain", "--json")
    check("exit 0", code == 0, code)
    doc = json.loads(out)
    check("no config", doc["config"] is None and doc["scenario"] is None, doc)
    check("every detector", [d["id"] for d in doc["detectors"]] == [d.id for d in SHIPPED], doc)
    for d in doc["detectors"]:
        for p in d["params"]:
            check(f"{d['id']}.{p['name']}: every field",
                  set(p) == {"name", "value", "unit", "source", "default", "meaning",
                             "calibrate"}, p)
        check(f"{d['id']}: its columns", d["columns"] and all(c["meaning"] for c in d["columns"]),
              d)


def test_a_terminal_gets_the_words_wrapped_and_a_pipe_a_line_each():
    e = explain_mod.entries([d for d in SHIPPED if d.id == "frame_jank"], None)[0]
    piped = explain_mod.one(e, None, None)
    meaning = e["params"][0]["meaning"]
    check("a pipe: the words on one line, to grep",
          any(ln.strip() == meaning for ln in piped.splitlines()), piped)
    wrapped = explain_mod.one(e, None, None, width=60)
    words = [ln for ln in wrapped.splitlines() if ln.startswith("      ")]
    check("a terminal: wrapped to its width", words and all(len(ln) <= 60 for ln in words),
          wrapped)
