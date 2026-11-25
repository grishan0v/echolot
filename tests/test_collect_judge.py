#!/usr/bin/env python3
"""`runner.judge`: whether one repeat counts, and why not (#996).

`collect` records a repeat (`record_one`), judges it, and keeps it in the set
or sets it aside. The judgement reads nothing from the phone, so each case
here is a repeat as `record_one` would hand it over, and the verdict it gets:
no device, no fake adb, no loop to drive to reach one branch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import runner  # noqa: E402
from echolot.screen import Screen  # noqa: E402

PACKAGE = "com.example.app"
ACTIVITY = f"{PACKAGE}/.MainActivity"
SHOWN = Screen(wakefulness="Awake", locked=False, powered=True)


def _repeat(info: dict, *, mode: str = "launch", reset: str = "force-stop",
            iterations: int = 3, left: str = "", again: str = "",
            after: Screen | None = None, ended_before: str | None = None) -> runner.Repeat:
    p = runner.Prepared(
        package=PACKAGE, name="startup", mode=mode, iterations=iterations,
        reset=reset, duration_ms=12000, config="", names=False, device="SERIAL",
        activity=ACTIVITY if mode == "launch" else None,
        command=None if mode == "launch" else "./scenario.sh",
        planted=None, settings={}, last=SHOWN, ended_before=ended_before)
    return runner.Repeat(round=p, i=0, attempt=1, info=info, left=left, was="stopped",
                         again=again, restops=1 if again else 0, waited=0.0,
                         cooldown=None, before=SHOWN, after=after)


COLD = {"launch_state": "COLD", "total_time_ms": 700, "recorded_after_ms": 11000,
        "activity": runner.component(ACTIVITY)}

# The first look at the set's first trace, as `main._scenario_ran` says it.
NO_FRAME = ("the app drew no frame in the scenario's window", None)
UNFINISHED = ("the end anchor `screen_shown` opened and never closed in its recording",
              None)
NO_END = (None, "the end anchor `screen_shown` did not occur in repeat 1")

# (case, repeat, look, outcome, cold, the reason or a note holds)
CASES = [
    ("a cold start counts",
     _repeat(COLD), None, "keep", True, None),
    ("a warm start after the stop is set aside (#404, #523)",
     _repeat({**COLD, "launch_state": "WARM"}), None, "aside", False, "not a cold start"),
    ("a pid left after the stop is not cold, whatever am start said (#404)",
     _repeat({**COLD, "launch_state": "HOT"}, left="4242"), None, "aside", False,
     "running again before the recording began (pid 4242)"),
    ("am start that started nothing stops the round (#385)",
     _repeat({"launch_error": "Error: Activity class {x} does not exist."}), None,
     "stop", None, "started nothing: Error: Activity class"),
    ("no start measured behind a screen that locked stops the round (#593)",
     _repeat({"launch_state": "UNKNOWN (0)"},
             after=Screen(wakefulness="Awake", locked=True)),
     None, "stop", None, "locked after the check before the repeat"),
    ("no start measured on a screen that is on is kept, and said (#593)",
     _repeat({"launch_state": "UNKNOWN (0)"}, after=SHOWN), None, "keep", None,
     "this trace may hold none"),
    ("with nothing stopping the app, a start of 0 ms holds no launch (#593)",
     _repeat({"launch_state": "UNKNOWN (0)", "total_time_ms": 0}, reset="none"),
     None, "keep", None, "this trace holds no launch"),
    ("a launch that ended on another activity is said (#909)",
     _repeat({**COLD, "activity": f"{PACKAGE}/{PACKAGE}.Onboarding"}), None, "keep",
     True, "the launch ended on"),
    ("the first trace whose app drew no frame stops the round (#849)",
     _repeat(COLD), NO_FRAME, "stop", None, "drew no frame"),
    ("the first trace that never reached its screen stops the round (#536)",
     _repeat(COLD), UNFINISHED, "stop", None, "did not get where it was going"),
    ("in a round of one, a first trace that never got there is kept, to look at",
     _repeat(COLD, iterations=1), UNFINISHED, "keep", True, "Look at the phone"),
    ("an end anchor that never occurred is said, and the round goes on",
     _repeat(COLD), NO_END, "keep", True, "did not occur in repeat 1"),
    ("command mode: nothing to judge the start by",
     _repeat({}, mode="command"), None, "keep", None, None),
]


@pytest.mark.parametrize("case, repeat, look, outcome, cold, holds", CASES,
                         ids=[c[0] for c in CASES])
def test_judge(case, repeat, look, outcome, cold, holds):
    verdict = runner.judge(repeat, look=look)
    assert verdict.outcome == outcome, case
    assert verdict.cold == cold, case
    said = " ".join(filter(None, [verdict.reason, *verdict.notes]))
    if holds is None:
        assert verdict.reason is None and verdict.notes == [], (case, said)
    else:
        assert holds in said, (case, said)
    if outcome == "stop":
        assert verdict.reason, case


def test_a_repeat_set_aside_is_not_looked_at():
    """The look is for the set's first trace: a warm repeat before it is set
    aside whatever the look would say."""
    verdict = runner.judge(_repeat({**COLD, "launch_state": "WARM"}), look=NO_FRAME)
    assert verdict.outcome == "aside"


def test_the_activity_the_repeat_before_ended_on_is_said_once():
    """A splash that hands over to the main activity does that in every
    repeat, and one line says it (#909)."""
    onboarding = f"{PACKAGE}/{PACKAGE}.Onboarding"
    verdict = runner.judge(_repeat({**COLD, "activity": onboarding},
                                   ended_before=onboarding))
    assert verdict.notes == []


def test_judge_reads_nothing_from_the_phone(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("judge ran adb")

    monkeypatch.setattr(runner, "_run", refuse)
    for _, repeat, look, *_ in CASES:
        runner.judge(repeat, look=look)
