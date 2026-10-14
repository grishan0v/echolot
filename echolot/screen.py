"""The phone's screen, on and unlocked before a launch (#369).

A launch behind a dark, locked screen starts nothing that can be measured. On
an SM-A515F connected over USB and not charging, its screen dozing and its
lock screen up, `am start -W` answered `LaunchState: UNKNOWN (0)` and no
`TotalTime`. The traces held the app's process and no `reportFullyDrawn`,
and neither `collect` nor the report said why.

So in launch and command modes `collect` reads the screen before the set and
again before each repeat, with one adb call:

- `mWakefulness` from `dumpsys power`: `Awake`, or `Asleep`, `Dozing`,
  `Dreaming`. A screen that is not awake is woken,
  `input keyevent KEYCODE_WAKEUP`;
- the lock screen from `dumpsys window policy`, where the keyguard's own
  monitor says whether it is showing. A lock screen is asked to go,
  `wm dismiss-keyguard`, which it does when there is no PIN on it. One that
  stays stops `collect` with a sentence: no command unlocks a phone with a
  PIN;
- `mIsPowered` and `mStayOn`, which say whether
  `stay_on_while_plugged_in` holds the screen on: only while the phone
  charges. A screen that went off between repeats is said with them.

On the A51 the read takes about half a second, before the app is stopped
and the recording starts. What the device does not print is not guessed at:
a dumpsys without the line leaves that half of the check to the launch, and
`am start -W` answering `UNKNOWN` after a `force-stop` is read against the
screen again (runner.py).

Every adb call goes through the `run` the runner hands in.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from .runner import RunnerError

# Long enough for the screen to come on, and for the lock screen to go.
SETTLE_S = 1.0

_WAKEFULNESS = re.compile(r"\bmWakefulness=(\w+)")
_POWERED = re.compile(r"\bmIsPowered=(true|false)")
_STAY_ON = re.compile(r"\bmStayOn=(true|false)")
# Where the lock screen is said. An SM-A515F (Android 13) prints it in the
# policy section under `KeyguardStateMonitor`, as `mIsShowing`. The other two
# are read for builds that print it under those names, older ones as
# `mShowingLockscreen`. The first one a device prints decides.
_LOCKED = (re.compile(r"\bmIsShowing=(true|false)"),
           re.compile(r"\bisKeyguardShowing=(true|false)"),
           re.compile(r"\bmShowingLockscreen=(true|false)"))


@dataclass(frozen=True)
class Screen:
    """What the device said of its screen; None where it said nothing."""

    wakefulness: str | None = None
    locked: bool | None = None
    powered: bool | None = None
    stay_on: bool | None = None

    @property
    def dark(self) -> bool:
        """Known not to be awake."""
        return self.wakefulness is not None and self.wakefulness != "Awake"

    def said(self) -> str:
        """The state as a phrase: `dozing, locked, not charging`."""
        parts = [self.wakefulness.lower() if self.wakefulness else "screen state unknown"]
        if self.locked is not None:
            parts.append("locked" if self.locked else "unlocked")
        if self.powered is not None:
            parts.append("charging" if self.powered else "not charging")
        return ", ".join(parts)


def _flag(pattern: re.Pattern, text: str) -> bool | None:
    m = pattern.search(text)
    return None if m is None else m.group(1) == "true"


def parse(text: str) -> Screen:
    """`dumpsys power` and `dumpsys window policy`, read together."""
    m = _WAKEFULNESS.search(text)
    locked = None
    for pattern in _LOCKED:
        locked = _flag(pattern, text)
        if locked is not None:
            break
    return Screen(wakefulness=m.group(1) if m else None, locked=locked,
                  powered=_flag(_POWERED, text), stay_on=_flag(_STAY_ON, text))


def read(device: str, run: Callable[..., str]) -> Screen:
    return parse(run(["adb", "-s", device, "shell",
                      "dumpsys power; dumpsys window policy"], timeout=30))


def _why_dark(s: Screen) -> str:
    """Why a screen went off during a set, as far as the device says."""
    if s.powered is False:
        return (" The phone is not charging, and `stay_on_while_plugged_in` "
                "keeps the screen on only while it charges.")
    if s.powered and s.stay_on is False:
        return (" The phone charges, and `stay_on_while_plugged_in` does not "
                "keep its screen on: Developer options, Stay awake.")
    return ""


def locked_sentence(device: str, s: Screen, during: str | None = None,
                    went_dark: bool = False) -> str:
    """The sentence `collect` stops with, the screen's state in it."""
    when = f"{during}: " if during else ""
    went = f" Its screen went off during the set.{_why_dark(s)}" if went_dark else ""
    return (f"{when}{device} is locked ({s.said()}), and its lock screen asks "
            f"for what collect cannot enter: a PIN, a pattern or a password. "
            f"Unlock it and collect again. A launch behind the lock screen "
            f"starts nothing that can be measured: `am start -W` answers "
            f"`LaunchState: UNKNOWN`.{went}")


def unmeasured_sentence(device: str, s: Screen, during: str) -> str:
    """A launch `am start -W` measured no start for, with the screen to blame.

    The check before the repeat found the screen on and unlocked, so it went
    off since: the repeat's trace is not kept. Nothing was asked of the lock
    screen here, so nothing is said of what it asks for.
    """
    became = "locked" if s.locked else "went dark"
    return (f"{during}: `am start -W` measured no start (`LaunchState: "
            f"UNKNOWN`): {device} {became} after the check before the repeat "
            f"({s.said()}), and a launch behind a dark or locked screen starts "
            f"nothing that can be measured.{_why_dark(s)} "
            f"{'Unlock it' if s.locked else 'Turn it on'} and collect again; this "
            f"repeat's trace is not kept.")


def ready(device: str, run: Callable[..., str], log: Callable[[str], None] = print,
          *, during: str | None = None,
          sleep: Callable[[float], None] = time.sleep) -> Screen:
    """The screen woken and the lock screen dismissed, or a sentence.

    `during` names the repeat when the set is under way, `before repeat 3 of
    8`. Silent when the screen is on and unlocked: a line only for what was
    done.
    """
    s = first = read(device, run)
    woke = dismissed = False
    if s.dark:
        run(["adb", "-s", device, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
        sleep(SETTLE_S)
        woke = True
        s = read(device, run)
    if s.locked:
        run(["adb", "-s", device, "shell", "wm", "dismiss-keyguard"])
        sleep(SETTLE_S)
        dismissed = True
        s = read(device, run)
    went_dark = bool(during) and first.dark
    if s.locked:
        raise RunnerError(locked_sentence(device, s, during, went_dark))
    if s.dark:
        raise RunnerError(
            f"{during + ': ' if during else ''}the screen of {device} did not "
            f"wake ({s.said()}). Turn it on and collect again: a launch on a "
            f"dark screen starts nothing that can be measured.")
    if woke or dismissed:
        done = [f"woke it, {first.wakefulness.lower()}"] if woke else []
        if dismissed:
            done.append("dismissed its lock screen, which asked for nothing")
        where = f" {during}" if during else ""
        log(f"  screen{where}: {' and '.join(done)}."
            + (f" It went off during the set.{_why_dark(first)}" if went_dark else ""))
    return s
