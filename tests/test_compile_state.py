#!/usr/bin/env python3
"""`environment.compile`: how ART had compiled the app a trace ran (#415).

What `analyze` reads off the trace is held by the self-check on the fixture
("compile state: …"). Here: what the merge, the report's header and
`compare` make of it, with the states two real builds of one large app had
on an SM-A515F — its benchmark build, compiled from its baseline profile at
install (`speed-profile`, `install-dm`), and a debuggable build of it that
nothing had compiled (`run-from-apk`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare, report  # noqa: E402
from tests.support import check  # noqa: E402


def state(filter_: str | None, reason: str | None = "install-dm",
          methods: int = 7, jit_ms: float = 97.0, classes: int = 0,
          verify_ms: float = 0.0, main_ms: float = 0.0) -> dict:
    return {"filter": filter_, "reason": reason if filter_ else None,
            "status": "up-to-date" if filter_ else None,
            "jit": {"methods": methods, "ms": jit_ms},
            "verified": {"classes": classes, "ms": verify_ms, "main_ms": main_ms}}


COMPILED = state("speed-profile")
NOTHING = state("run-from-apk", "unknown", methods=14000, jit_ms=6700.0,
                classes=7400, verify_ms=4000.0, main_ms=3900.0)


# --- the merge across repeats ---------------------------------------------------

def test_repeats_that_agree_keep_their_state_and_take_medians() -> None:
    merged = report._merge_compile([state("speed-profile", methods=5, jit_ms=90.0),
                                    state("speed-profile", methods=7, jit_ms=97.0),
                                    state("speed-profile", methods=9, jit_ms=120.0)])
    check("the state every repeat agrees on, and the JIT's medians",
          merged["filter"] == "speed-profile" and merged["reason"] == "install-dm"
          and merged["jit"] == {"methods": 7, "ms": 97.0} and merged["runs"] == "3/3",
          merged)


def test_repeats_that_disagree_are_named_together() -> None:
    merged = report._merge_compile([COMPILED, NOTHING, None])
    check("both filters, and their reasons in the same order, pair by pair",
          merged["filter"] == "run-from-apk and speed-profile"
          and merged["reason"] == "unknown and install-dm", merged)
    check("runs counts the repeats whose trace held the state",
          merged["runs"] == "2/3", merged)


def test_a_trace_without_the_slice_counts_as_not_holding_it() -> None:
    merged = report._merge_compile([state(None), state(None)])
    check("no state, and no repeat said to hold one",
          merged["filter"] is None and merged["runs"] == "0/2", merged)
    check("nothing at all is nothing", report._merge_compile([None, None]) is None)


# --- the header -------------------------------------------------------------------

def test_a_compiled_app_is_one_line() -> None:
    lines = report._compile_lines(state("speed-profile", classes=402, verify_ms=316.0,
                                        main_ms=12.0))
    check("one line, no warning", len(lines) == 1, lines)
    check("the state, the JIT and the verification",
          lines[0] == "Compiled: **speed-profile** (install-dm) · JIT 7 methods, 97 ms "
                      "· 402 classes verified at run time, 316 ms, 12 ms of it on the "
                      "main thread", lines)


def test_an_app_nothing_compiled_is_warned_about() -> None:
    lines = report._compile_lines(NOTHING)
    check("the line, then the warning", len(lines) == 2
          and lines[1].startswith("> ⚠️ The app ran with `filter=run-from-apk`")
          and "dumpsys package dexopt" in lines[1], lines)
    verified = report._compile_lines(state("verify", "install"))
    check("verified only is no better", any("`filter=verify`" in x for x in verified),
          verified)


def test_repeats_on_two_states_are_warned_about() -> None:
    lines = report._compile_lines(report._merge_compile([COMPILED, NOTHING]))
    check("mixed repeats warn, and the uncompiled half warns too",
          any(x.startswith("> ⚠️ The repeats ran on different compile states") for x in lines)
          and any("`filter=run-from-apk` in some repeats" in x for x in lines), lines)


def test_a_process_already_running_says_so() -> None:
    lines = report._compile_lines(state(None, methods=3, jit_ms=12.0))
    check("no state, said in words, the JIT still counted",
          lines == ["Compiled: not in this trace, the process was already running "
                    "when it began · JIT 3 methods, 12 ms"], lines)
    check("nothing recorded, nothing said", report._compile_lines(None) == [])


# --- compare ------------------------------------------------------------------------

def rep(block) -> dict:
    return {"environment": {"compile": block}}


def test_compare_warns_when_the_rounds_were_compiled_differently() -> None:
    warned = compare._compile_moved(rep(COMPILED), rep(NOTHING))
    check("one warning, both states named",
          [w["id"] for w in warned] == ["compile"]
          and "`speed-profile` (install-dm) before, `run-from-apk` (unknown) after"
          in warned[0]["text"], warned)
    widened = compare._compile_moved(rep(COMPILED), rep(state("speed-profile", "bg-dexopt")))
    check("the same filter from another profile is another build",
          [w["id"] for w in widened] == ["compile"], widened)


def test_compare_is_quiet_when_it_cannot_know_or_nothing_moved() -> None:
    check("alike is quiet", compare._compile_moved(rep(COMPILED), rep(COMPILED)) == [])
    check("a report from before the field is unknown, and quiet",
          compare._compile_moved({"environment": {}}, rep(NOTHING)) == [])
    check("a side whose traces held no state is unknown, and quiet",
          compare._compile_moved(rep(None), rep(NOTHING)) == []
          and compare._compile_moved(rep(state(None)), rep(NOTHING)) == [])


def test_compare_warns_about_a_side_whose_repeats_disagree() -> None:
    mixed = report._merge_compile([COMPILED, NOTHING])
    warned = compare._compile_moved(rep(mixed), rep(mixed))
    check("each mixed side, by name", [w["text"].split("'")[0] for w in warned] == [
        "The before side", "The after side"], warned)
