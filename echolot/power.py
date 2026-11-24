"""The phone's power, before a set and between its repeats (#524).

A phone that is not charging records its set on a battery that drains, and
one that runs low turns battery saver on, which the platform hands to the
power HAL as a hint to save power and which slows the CPU on many phones. An
SM-A515F ran a whole session on battery, 49% to 45%, and a later hunt on it
went from 44% to 39%. `collect` read `mIsPowered` before every repeat for the
screen's sake (screen.py) and said nothing of it; the agent asked adb itself.
Macrobenchmark refuses to start below 25% (`LOW-BATTERY`), charging or not;
launch and command modes had no floor at all.

So, from the read screen.py makes before the set and again before each
repeat, at no adb call of its own:

- a phone that is not charging and is under `runner.min_battery` (25, the
  Macrobenchmark's mark; 0 turns the check off) stops `collect` before
  anything is recorded. A charging phone is not stopped: its level rises;
- a phone that is not charging above it gets one line, and so does battery
  saver. A charging phone with saver off gets none;
- a change between repeats, the cable out or saver on, is said before the
  repeat that found it;
- each trace's account (recording.py) keeps `power` and `battery_saver`, which
  `compare` sets against the other side's, and the level.

What the device does not print is unknown, never "charging" or "off".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .runner import RunnerError
from .screen import Screen

# Macrobenchmark's floor, in percent.
FLOOR = 25


def floor(section: dict) -> int:
    """`runner.min_battery`, the charge under which a phone that is not
    charging is not recorded; 0 turns the check off."""
    value = section.get("min_battery", FLOOR)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= 100):
        raise RunnerError(
            f"runner.min_battery: {value!r} is not a percentage. 0 to 100, in "
            f"digits; 0 turns the check off.")
    return int(value)


def phrase(s: Screen) -> str:
    """`not charging, 49%`, `charging, 87%, battery saver on`; empty when the
    device said nothing of it."""
    parts = []
    if s.powered is not None:
        parts.append("charging" if s.powered else "not charging")
    if s.level is not None:
        parts.append(f"{s.level}%")
    if s.saver:
        parts.append("battery saver on")
    return ", ".join(parts)


def low(s: Screen, under: int) -> bool:
    """Not charging, and under the floor."""
    return s.powered is False and s.level is not None and s.level < under


def low_sentence(device: str, s: Screen, under: int) -> str:
    return (f"{device} is not charging, and its battery is at {s.level}%: under the "
            f"{under}% collect records from (runner.min_battery). The set would "
            f"drain it further, and a phone that runs low turns battery saver on "
            f"and slows down partway through; Macrobenchmark refuses below 25% "
            f"for the same reason. Plug the phone in and collect again, or set "
            f"runner.min_battery: 0 in local.yml to record on battery anyway.")


def check(device: str, s: Screen, under: int, log: Callable[[str], None]) -> None:
    """Before the set: the stop under the floor, else a line for what is not
    as it should be. Silent for a charging phone with saver off."""
    if under and low(s, under):
        raise RunnerError(low_sentence(device, s, under),
                          gist=f"{device} is not charging, battery at {s.level}% "
                               f"(runner.min_battery: {under})")
    if s.powered is False:
        log(f"  [!] power: {device} is {phrase(s)}. The set runs on a draining "
            f"battery, and compare tells it from a set recorded while charging; "
            f"plug the phone in to record alike")
    elif s.saver:
        log(f"  [!] power: {device} is {phrase(s)}. Saver slows the phone, and "
            f"compare tells this set from one recorded without it; turn it off "
            f"to record alike")


def between(device: str, last: Screen, now: Screen, during: str,
            log: Callable[[str], None]) -> None:
    """A change since the read before, said once, where it was found."""
    moved = [(b, n) for b, n in ((last.powered, now.powered), (last.saver, now.saver))
             if b is not None and n is not None and b != n]
    if moved:
        log(f"  [!] power {during}: {phrase(now)}, and {phrase(last)} before. The "
            f"set's repeats are no longer recorded alike; each trace's account "
            f"says how it was, and compare says so")


def facts(s: Screen) -> dict[str, Any]:
    """What a trace's account keeps of the power it was recorded on."""
    return {
        "power": None if s.powered is None else ("charging" if s.powered else "not charging"),
        "battery_saver": None if s.saver is None else ("on" if s.saver else "off"),
        "battery_level": s.level,
    }
