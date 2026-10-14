#!/usr/bin/env python3
"""The phone's screen, on and unlocked before a launch (echolot/screen.py, #369).

The dumps are the lines an SM-A515F (Android 13) printed. Connected over USB
and not charging, its screen dozing and its lock screen up, it took two
launches that `am start -W` answered `LaunchState: UNKNOWN (0)`, and two
traces with no start in them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import runner, screen  # noqa: E402
from echolot.runner import RunnerError  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_collect_fake_adb import (  # noqa: E402
    AM_START_UNMEASURED, PACKAGE, SCREEN_ON, SERIAL, FakeAdb, old_set)

DOZING_LOCKED = ("  mWakefulness=Dozing\n  mIsPowered=false\n  mPlugType=0\n  mStayOn=false\n"
                 "    mShowingDream=false mDreamingLockscreen=true\n"
                 "    isKeyguardShowing=true\n")
AWAKE_LOCKED = ("  mWakefulness=Awake\n  mIsPowered=false\n  mStayOn=false\n"
                "    isKeyguardShowing=true\n")
AWAKE_UNLOCKED = ("  mWakefulness=Awake\n  mIsPowered=false\n  mStayOn=false\n"
                  "    isKeyguardShowing=false\n")


@pytest.fixture
def adb(monkeypatch) -> FakeAdb:
    fake = FakeAdb()
    monkeypatch.setattr(runner, "_run", fake)
    monkeypatch.setattr(screen, "SETTLE_S", 0)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    return fake


# --- what the device says ----------------------------------------------------

def test_the_screen_as_dumpsys_says_it() -> None:
    s = screen.parse(DOZING_LOCKED)
    check("the A51 behind its lock screen", s == screen.Screen(
        wakefulness="Dozing", locked=True, powered=False, stay_on=False), s)
    check("dark", s.dark)
    check("said in words", s.said() == "dozing, locked, not charging", s.said())
    check("on and unlocked", screen.parse(SCREEN_ON) == screen.Screen(
        wakefulness="Awake", locked=False, powered=True, stay_on=True))
    check("an older platform's line for the lock screen",
          screen.parse("mWakefulness=Awake\nmShowingLockscreen=true\n").locked is True)
    check("the keyguard's own monitor",
          screen.parse("KeyguardStateMonitor\n  mIsShowing=false\n").locked is False)
    unknown = screen.parse("")
    check("nothing printed, nothing known, and nothing to act on",
          unknown == screen.Screen() and not unknown.dark and unknown.locked is None)


# --- before the set ----------------------------------------------------------

def test_a_screen_on_and_unlocked_is_read_once_and_said_nothing_of(adb) -> None:
    said: list[str] = []
    screen.ready(SERIAL, runner._run, said.append)
    check("one read", adb.kinds() == ["screen"], adb.kinds())
    check("and no line", said == [], said)


def test_a_dark_screen_is_woken_and_a_lock_without_a_pin_dismissed(adb) -> None:
    adb.script["screen"] = [DOZING_LOCKED, AWAKE_LOCKED, AWAKE_UNLOCKED]
    said: list[str] = []
    s = screen.ready(SERIAL, runner._run, said.append)
    check("woken, then the lock screen asked to go, each read after",
          adb.kinds() == ["screen", "wake", "screen", "dismiss", "screen"], adb.kinds())
    check("on and unlocked after", not s.dark and s.locked is False, s)
    check("a line for what was done",
          said == ["  screen: woke it, dozing and dismissed its lock screen, "
                   "which asked for nothing."], said)


def test_a_lock_screen_that_asks_for_a_pin_stops_collect(adb) -> None:
    adb.script["screen"] = [DOZING_LOCKED, AWAKE_LOCKED]
    with pytest.raises(RunnerError) as e:
        screen.ready(SERIAL, runner._run, lambda m: None)
    said = str(e.value)
    check("the device and its state", f"{SERIAL} is locked (awake, locked, not charging)"
          in said, said)
    check("what collect cannot do, and what to do",
          "a PIN, a pattern or a password" in said and "Unlock it and collect again" in said,
          said)
    check("and why it matters", "`LaunchState: UNKNOWN`" in said, said)
    check("before the set nothing is said of the set", "during the set" not in said, said)


def test_a_screen_that_went_dark_during_the_set_says_why(adb) -> None:
    adb.script["screen"] = [DOZING_LOCKED, AWAKE_UNLOCKED]
    said: list[str] = []
    screen.ready(SERIAL, runner._run, said.append, during="before repeat 3 of 8")
    check("woken, with the repeat and the reason",
          said == ["  screen before repeat 3 of 8: woke it, dozing. It went off during "
                   "the set. The phone is not charging, and `stay_on_while_plugged_in` "
                   "keeps the screen on only while it charges."], said)

    adb.script["screen"] = [DOZING_LOCKED, AWAKE_LOCKED]
    with pytest.raises(RunnerError) as e:
        screen.ready(SERIAL, runner._run, lambda m: None, during="before repeat 3 of 8")
    check("locked on the way: the repeat, the lock, and the charge",
          str(e.value).startswith("before repeat 3 of 8: ")
          and "Its screen went off during the set. The phone is not charging"
          in str(e.value), str(e.value))


def test_a_device_that_says_nothing_is_left_as_it_is(adb) -> None:
    adb.script["screen"] = [""]
    said: list[str] = []
    check("nothing known", screen.ready(SERIAL, runner._run, said.append) == screen.Screen())
    check("nothing done", adb.kinds() == ["screen"] and said == [], (adb.kinds(), said))


# --- in collect --------------------------------------------------------------

def test_a_locked_phone_stops_collect_before_anything_is_touched(tmp_path, adb) -> None:
    out = tmp_path / "traces"
    before = old_set(out)
    adb.script["screen"] = [DOZING_LOCKED, AWAKE_LOCKED]
    with pytest.raises(RunnerError, match="is locked"):
        runner.collect(PACKAGE, out, 3, section={}, name="startup", log=lambda m: None)
    kinds = adb.kinds()
    check("nothing stopped, recorded or launched",
          not {"force-stop", "perfetto", "start"} & set(kinds), kinds)
    check("and the set from before is where it was",
          sorted(p.name for p in out.iterdir()) == before, list(out.iterdir()))


def test_a_screen_that_went_dark_between_repeats_is_woken_and_the_set_goes_on(
        tmp_path, adb) -> None:
    # Before the set and before the first repeat on; dark before the second.
    adb.script["screen"] = [SCREEN_ON, SCREEN_ON, DOZING_LOCKED, AWAKE_UNLOCKED, SCREEN_ON]
    said: list[str] = []
    results = runner.collect(PACKAGE, tmp_path / "traces", 3, section={},
                             name="startup", log=said.append)
    check("three traces", len(results) == 3, results)
    check("one wake, before the second repeat",
          adb.kinds().count("wake") == 1
          and adb.kinds().index("wake") > adb.kinds().index("pull"), adb.kinds())
    check("said with the repeat and the reason",
          any(line.startswith("  screen before repeat 2 of 3: woke it, dozing. It went off "
                              "during the set. The phone is not charging")
              for line in said), said)


def test_a_launch_behind_a_lock_that_came_down_stops_at_that_repeat(tmp_path, adb) -> None:
    """The check before the repeat found the screen on, and the screen locked
    before the launch: the second launch measured no start."""
    out = tmp_path / "traces"
    adb.script["screen"] = [SCREEN_ON, SCREEN_ON, SCREEN_ON, DOZING_LOCKED]
    adb.script["start"] = [AM_START_UNMEASURED.replace("UNKNOWN (0)", "COLD")
                           + "TotalTime: 523\n", AM_START_UNMEASURED]
    with pytest.raises(RunnerError) as e:
        runner.collect(PACKAGE, out, 3, section={}, name="startup", log=lambda m: None)
    said = str(e.value)
    check("said at the repeat, with the lock and the charge",
          said.startswith("repeat 2 of 3: `am start -W` measured no start")
          and f"{SERIAL} locked after the check before the repeat (dozing, locked, "
              "not charging)" in said
          and "The phone is not charging" in said and "Unlock it" in said, said)
    check("and nothing claimed of a lock screen nobody asked",
          "a PIN" not in said, said)
    kinds = adb.kinds()
    check("the second recording waited out and removed, and never pulled",
          kinds.count("pull") == 1 and kinds[-2:] == ["screen", "rm"], kinds)
    check("the first repeat's trace is kept",
          sorted(p.name for p in out.iterdir()) == ["startup_iter000.perfetto-trace"],
          list(out.iterdir()))


def test_a_launch_that_measured_no_start_on_an_unlocked_screen_is_kept_and_said(
        tmp_path, adb) -> None:
    adb.script["start"] = [AM_START_UNMEASURED]
    said: list[str] = []
    results = runner.collect(PACKAGE, tmp_path / "traces", 1, section={},
                             name="startup", log=said.append)
    check("kept", len(results) == 1 and results[0]["launch_state"] == "UNKNOWN (0)", results)
    check("and said under its line",
          any("am start -W measured no start, with the screen on and unlocked" in line
              for line in said), said)


def test_without_a_force_stop_an_unknown_launch_is_not_read_against_the_screen(
        tmp_path, adb) -> None:
    """`reset_policy: none` launches into an app that may be in front, and
    the system answers `UNKNOWN` for a launch that started nothing."""
    adb.script["start"] = [AM_START_UNMEASURED]
    said: list[str] = []
    runner.collect(PACKAGE, tmp_path / "traces", 1, section={"reset_policy": "none"},
                   name="startup", log=said.append)
    check("no read after the launch", adb.kinds()[-3:] == ["start", "wait", "pull"],
          adb.kinds())
    check("and no line about it", not any("measured no start" in line for line in said),
          said)
