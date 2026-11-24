#!/usr/bin/env python3
"""A detector says what it reads, and the report says when the trace lacked it (#441).

Three things are held here, none of which needs trace_processor. The header
`-- @needs:` is parsed beside `@why`, and a name that is no source is refused
when the detector loads. What each shipped detector declares is pinned, so that
a change to it is a decision somebody made. And the two keys a detector's entry
carries, `checked` and `reason`, are decided for one trace.

Merging them across repeats and what the report says are in
test_needs_report.py. How each source is told from a real trace is held in the
self-check (`echolot/selftest.py`), one case per source.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

from echolot import needs as needs_mod  # noqa: E402
from echolot.main import DETECTOR_DIR  # noqa: E402
from echolot.tp import Detector, load_detectors, parse_meta  # noqa: E402

# What every shipped detector reads, from reading its query. The ones that read
# only what every recording `collect` makes holds — the app's slices, its
# threads' states, who woke whom — are not here, and `test_the_rest_declare_nothing`
# holds them to it.
DECLARED = {
    "anr": ("atrace:am",),
    "app_init": ("atrace:am", "process_start"),
    "binder_txn": ("atrace:binder_driver",),
    "composables": ("composable_names",),
    "frame_jank": ("frame_timeline",),
    "gc_pressure": ("atrace:dalvik",),
    "io_wait": ("platform_state",),
    "main_thread_kernel_waits": ("platform_state",),
    "monitor_contention": ("atrace:dalvik",),
}


def _write(tmp_path: Path, header: str) -> Path:
    path = tmp_path / "d.sql"
    path.write_text(header + "\nSELECT 'x' AS location LIMIT 1;\n", encoding="utf-8")
    return path


# --- the header ------------------------------------------------------------------

def test_needs_is_parsed_beside_why(tmp_path: Path) -> None:
    path = _write(tmp_path, "-- @id: d\n-- @title: D\n"
                            "-- @why: it reads two things,\n--       and says so\n"
                            "-- @needs: platform_state, atrace:am\n"
                            "-- @param: min_ms = 5\n")
    d = Detector.from_file(path)
    check("both sources are read, in order", d.needs == ("platform_state", "atrace:am"),
          d.needs)
    check("and @why keeps its own words", d.why == "it reads two things, and says so",
          d.why)
    check("the params after it are still read", d.params == {"min_ms": 5}, d.params)


def test_a_detector_with_no_needs_declares_nothing(tmp_path: Path) -> None:
    d = Detector.from_file(_write(tmp_path, "-- @id: d\n-- @why: reads slices\n"))
    check("nothing is asked of the trace", d.needs == (), d.needs)


def test_a_repeated_source_is_asked_once(tmp_path: Path) -> None:
    d = Detector.from_file(_write(tmp_path, "-- @id: d\n-- @needs: frame_timeline, frame_timeline\n"))
    check("once", d.needs == ("frame_timeline",), d.needs)


def test_a_name_that_is_no_source_is_refused_with_the_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as e:
        Detector.from_file(_write(tmp_path, "-- @id: d\n-- @needs: platform_sate\n"))
    said = str(e.value)
    check("it names the file and the name", "d.sql" in said and "platform_sate" in said, said)
    check("and lists the sources", "frame_timeline" in said and "atrace:am" in said, said)


def test_a_category_nobody_defined_is_refused() -> None:
    with pytest.raises(ValueError):
        needs_mod.parse("atrace:dalvk")


def test_meta_leaves_needs_to_the_detector() -> None:
    meta, _, _ = parse_meta("-- @id: d\n-- @needs: frame_timeline\n-- @why: w\n")
    check("it is a plain key to parse_meta", meta["needs"] == "frame_timeline", meta)


# --- what the shipped detectors read ------------------------------------------------

def test_the_shipped_detectors_declare_what_their_queries_read() -> None:
    got = {d.id: d.needs for d in load_detectors(DETECTOR_DIR) if d.needs}
    check("exactly these, and nothing else", got == DECLARED, got)


def test_the_rest_declare_nothing() -> None:
    shipped = {d.id for d in load_detectors(DETECTOR_DIR)}
    quiet = sorted(shipped - set(DECLARED))
    check("the ones that read only slices, thread states and wakeups",
          quiet == ["anr_risk", "main_thread_block", "main_thread_outlier",
                    "main_thread_waits", "repeated_work", "runnable_starvation",
                    "uninstrumented_cpu"], quiet)


def test_every_source_is_read_by_a_detector() -> None:
    used = {n for d in load_detectors(DETECTOR_DIR) for n in d.needs}
    check("a source nobody declares is dead weight",
          used == set(needs_mod.SOURCES), set(needs_mod.SOURCES) ^ used)


def test_needs_stands_straight_after_the_why_block() -> None:
    """Where the header puts it, so that two branches editing headers merge.

    `@why` may run over several lines; the line after the last is `@needs`.
    """
    for path in sorted(DETECTOR_DIR.glob("*.sql")):
        lines = path.read_text(encoding="utf-8").splitlines()
        needs = [i for i, line in enumerate(lines) if line.startswith("-- @needs:")]
        if not needs:
            continue
        check(f"{path.name}: one @needs", len(needs) == 1, needs)
        at = needs[0]
        above = lines[at - 1]
        check(f"{path.name}: it follows the @why block, a comment line of it",
              above.startswith("-- @why:") or above.startswith("--       "), above)
        why = max(i for i, line in enumerate(lines[:at]) if line.startswith("-- @why:"))
        check(f"{path.name}: and nothing else sits between",
              all(line.startswith("--       ") for line in lines[why + 1:at]),
              lines[why:at + 1])


# --- one trace: checked, and why not ------------------------------------------------

def test_a_detector_that_looked_is_checked() -> None:
    held = {"platform_state": None}
    check("held", needs_mod.verdict(("platform_state",), held) == {"checked": True})
    check("and one that needs nothing is", needs_mod.verdict((), held) == {"checked": True})


def test_a_detector_the_trace_did_not_feed_says_why() -> None:
    held = {"platform_state": "No state.", "frame_timeline": None}
    got = needs_mod.verdict(("platform_state", "frame_timeline"), held)
    check("it did not look, and the reason is the source's", got == {
        "checked": False, "reason": "No state."}, got)


def test_two_missing_sources_make_one_reason() -> None:
    got = needs_mod.verdict(("a", "b"), {"a": "No a.", "b": "No b."})
    check("both, in the order the detector declares them", got["reason"] == "No a. No b.", got)


def test_a_failed_detector_did_not_look_either() -> None:
    got = needs_mod.verdict(("platform_state",), {"platform_state": None}, "no such table")
    check("not checked", got["checked"] is False, got)
    check("and told to read its error", "error" in got["reason"], got)
    lacking = needs_mod.verdict(("platform_state",), {"platform_state": "No state."}, "boom")
    check("what the trace lacks comes first", lacking["reason"] == "No state.", lacking)


def test_every_source_has_a_sentence_a_person_can_read() -> None:
    probe = needs_mod.Probe(_Silent(), {})
    for name, source in needs_mod.SOURCES.items():
        said = source.missing(probe)
        check(f"{name}: one sentence", said.endswith(".") and said[0].isupper()
              and said.count(". ") == 0, said)
        check(f"{name}: in plain words, none of the tool's own",
              not any(w in said for w in ("atrace", "ftrace", "sched_", "slice",
                                          "trace_processor", "perfetto")), said)


class _Silent:
    """A trace that holds nothing and carries no config."""

    def query(self, sql: str) -> list:
        return []


def test_a_composable_name_that_never_arrived_says_whether_it_was_asked_for() -> None:
    asked = needs_mod.Probe(_Silent(), {"compose_names": {"asked": True, "calls": 0}})
    plain = needs_mod.Probe(_Silent(), {"compose_names": None})
    src = needs_mod.SOURCES["composable_names"]
    check("neither holds it", not src.holds(asked) and not src.holds(plain))
    check("one asked, and none came", "asked" in src.missing(asked), src.missing(asked))
    check("the other never did", "asked" not in src.missing(plain), src.missing(plain))
    named = needs_mod.Probe(_Silent(), {"compose_names": {"asked": True, "calls": 3}})
    check("calls on the main thread are the evidence", src.holds(named))


class _Config(_Silent):
    def __init__(self, text: str):
        self.text = text

    def query(self, sql: str) -> list:
        return [{"text": self.text}] if "trace_config_pbtxt" in sql else []


def test_a_category_is_held_when_the_recording_asked_for_it() -> None:
    text = 'atrace_categories: "am"\natrace_categories: "dalvik"\n'
    probe = needs_mod.Probe(_Config(text), {})
    check("asked for", needs_mod.SOURCES["atrace:am"].holds(probe))
    check("asked for too", needs_mod.SOURCES["atrace:dalvik"].holds(probe))
    check("not asked for", not needs_mod.SOURCES["atrace:binder_driver"].holds(probe))
    said = needs_mod.SOURCES["atrace:binder_driver"].missing(probe)
    check("and it says that", "did not ask" in said, said)


def test_a_recording_that_does_not_say_what_it_asked_for_is_not_taken_on_trust() -> None:
    probe = needs_mod.Probe(_Silent(), {})
    check("so no category is held", not needs_mod.SOURCES["atrace:am"].holds(probe))
    said = needs_mod.SOURCES["atrace:am"].missing(probe)
    check("and the reason says the recording is silent about it",
          "does not say" in said, said)


class _Never:
    def query(self, sql: str) -> list:
        raise RuntimeError("no such table")


def test_a_trace_that_cannot_answer_does_not_hold_the_source(capsys) -> None:
    probe = needs_mod.Probe(_Never(), {})
    check("a failed question is a no", not needs_mod.SOURCES["frame_timeline"].holds(probe))
    check("and is said on stderr", "[!] needs:" in capsys.readouterr().err)
