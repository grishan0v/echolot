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


# --- a debuggable build (#925) ---------------------------------------------------

def test_a_debuggable_build_is_said_and_not_sent_to_a_baseline_profile() -> None:
    every = report._merge_debuggable([True, True, None])
    check("counted over the repeats that said", every == {"repeats": 2, "of": 2}, every)
    lines = report._compile_lines(NOTHING, every)
    check("said first, as a debuggable build",
          lines[0].startswith("> ⚠️ This is a debuggable build: ART runs it interpreted"), lines)
    check("and no advice to record a build compiled from its profile",
          not any("baseline profile;" in x or "Record again on a build compiled" in x
                  for x in lines), lines)
    some = report._compile_lines(NOTHING, report._merge_debuggable([True, False, False]))
    check("some repeats: how many, and the filter's advice kept for the rest",
          some[0].startswith("> ⚠️ This is a debuggable build in 1 of 3 repeats")
          and any("Record again on a build compiled" in x for x in some), some)
    check("said without a compile state too",
          report._compile_lines(None, every)[0].startswith("> ⚠️ This is a debuggable build"))
    check("a build that is not: nothing more",
          report._compile_lines(COMPILED, report._merge_debuggable([False]))
          == report._compile_lines(COMPILED))
    check("nobody said: nothing", report._merge_debuggable([None, None]) is None)


def test_collect_reads_the_flags_in_the_settings_call() -> None:
    from echolot import recording
    asked = []

    def run(argv, timeout):
        asked.append(argv[-1])
        return ("animator_duration_scale=1.0\n"
                "    flags=[ DEBUGGABLE HAS_CODE ALLOW_CLEAR_USER_DATA ALLOW_BACKUP ]\n"
                "    privateFlags=[ PRIVATE_FLAG_ACTIVITIES_RESIZE_MODE_RESIZEABLE ]\n")
    got = recording.settings("R58M123ABC", run, "com.example.app")
    check("one call, the package's flags in it", len(asked) == 1
          and "dumpsys package com.example.app" in asked[0], asked)
    check("debuggable kept", got.get("debuggable") is True, got)
    check("pkgFlags of an older Android",
          recording.parse_debuggable("  pkgFlags=[ HAS_CODE ALLOW_BACKUP ]") is False)
    check("no flags line: unknown", recording.parse_debuggable("privateFlags=[ DEBUGGABLE ]")
          is None)
    check("and without a package, nothing asked of it",
          "debuggable" not in recording.settings("R58M123ABC", run))


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


def test_the_reason_alone_is_another_build_only_for_a_profile() -> None:
    """#809: `verify` after `adb install -r` (install) and after
    `cmd package compile -m verify -f` (cmdline) is the same code."""
    installed, compiled = rep(state("verify", "install")), rep(state("verify", "cmdline"))
    check("verify by install against verify by command is quiet",
          compare._compile_moved(installed, compiled) == []
          and compare.compiled_apart(installed, compiled) is None)
    check("a profile's reason still counts",
          compare.compiled_apart(rep(COMPILED), rep(state("speed-profile", "bg-dexopt")))
          == ("`speed-profile` (install-dm)", "`speed-profile` (bg-dexopt)"))
    check("another filter is apart whatever the reason",
          compare.compiled_apart(rep(state("speed-profile", "install")), compiled)
          == ("`speed-profile` (install)", "`verify` (cmdline)"))


def test_compare_is_quiet_when_it_cannot_know_or_nothing_moved() -> None:
    check("alike is quiet", compare._compile_moved(rep(COMPILED), rep(COMPILED)) == [])
    check("a report from before the field is unknown, and quiet",
          compare._compile_moved({"environment": {}}, rep(NOTHING)) == [])
    check("a side whose traces held nothing at all is unknown, and quiet",
          compare._compile_moved(rep(None), rep(NOTHING)) == [])
    check("a side without a state, with JIT counts alike, is quiet",
          compare._compile_moved(rep(state(None, methods=64)),
                                 rep(state(None, methods=70))) == [])


def test_compare_reads_the_jit_when_no_side_holds_the_state() -> None:
    """A scroll recorded with the process already up holds no compile state;
    its JIT count does (#592): 64 methods on the profile, 328 after `verify`,
    2 after `speed`."""
    for jb, ja, more in ((64, 328, "after"), (64, 2, "before")):
        warned = compare._compile_moved(rep(state(None, methods=jb)),
                                        rep(state(None, methods=ja)))
        check(f"{jb} against {ja}: one compile warning, both counts named",
              [w["id"] for w in warned] == ["compile"]
              and f"{jb} methods in the window before and {ja} after" in warned[0]["text"]
              and f"the {more} side" in warned[0]["text"], warned)
    one_known = compare._compile_moved(rep(COMPILED), rep(state(None, methods=328)))
    check("one side known and the other not: the JIT still speaks",
          [w["id"] for w in one_known] == ["compile"], one_known)
    check("both known and alike: the JIT is not asked",
          compare._compile_moved(rep(state("speed-profile", methods=64)),
                                 rep(state("speed-profile", methods=328))) == [])
    check("a few methods apart is not a finding",
          compare._compile_moved(rep(state(None, methods=2)),
                                 rep(state(None, methods=9))) == [])


def test_compare_warns_about_a_side_whose_repeats_disagree() -> None:
    mixed = report._merge_compile([COMPILED, NOTHING])
    warned = compare._compile_moved(rep(mixed), rep(mixed))
    check("each mixed side, by name", [w["text"].split("'")[0] for w in warned] == [
        "The before side", "The after side"], warned)


def test_analyze_says_a_debuggable_build_collect_kept(tmp_path: Path) -> None:
    """The trace does not say it; the account `collect` kept beside it does."""
    import contextlib
    import io
    import json

    from echolot import fixture, recorder, recording
    from echolot.main import main
    trace = tmp_path / "coldStart_iter000.perfetto-trace"
    trace.write_bytes(fixture.build())
    recording.write(trace, {"runner": "launch", "debuggable": True})
    config = tmp_path / "echolot.yml"
    config.write_text("project:\n  package: com.example.app\n  process: com.example.app\n",
                      encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["analyze", str(trace), "-c", str(config), "-o", str(tmp_path / "out")])
    check("exit 0", code == 0, err.getvalue()[-400:])
    rep = json.loads((tmp_path / "out/report.json").read_text(encoding="utf-8"))
    check("in the json", rep["environment"]["debuggable"] is True,
          rep["environment"].get("debuggable"))
    md = (tmp_path / "out/report.md").read_text(encoding="utf-8")
    check("and in the header", "This is a debuggable build" in md, md[:2000])
