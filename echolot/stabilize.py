"""The device's state around a set of repeats: cooled before each one, and
held still while they run.

A duration is the work done over the speed it was done at, and a phone does
not keep its speed for long. Eight launches recorded back to back on an
SM-A515F ran at a clock that fell repeat by repeat, from 2066 to 1915 MHz, and
five of them began with the kernel still throttling the big cluster after the
start before. Waiting before each one for the `cpu` sensor to read 34 °C held
the next eight at 2032 to 2049 MHz, none of them starting throttled. `compare`
says afterwards that two rounds ran at different clocks (`environment`); this
is the part that keeps a set from drifting in the first place. Four keys under
`runner:`, each off unless set:

    cooldown           before each repeat, wait until a sensor reads at or
                       below `max_c`
    fixed_performance  the platform's fixed performance mode, where the
                       device's power HAL has one
    airplane_mode      no radio while the set runs
    brightness         the screen at one level, auto-brightness off

Whatever is changed is put back when the set ends, whatever ended it: a
phone is somebody's phone between sets. A change the device refuses is said
and the set goes on, since a repeat recorded on a device in its usual state
is still a repeat; a sensor the device does not have stops the set before
anything is touched, since waiting on nothing would wait forever.

Every call goes through the `run` the runner hands in — its single door to
adb, the one the tests replace.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .runner import RunnerError

# What `cooldown.sensor` may name. `battery` is read from `dumpsys battery`,
# which every Android has. The rest are the types of the thermal HAL, which
# `dumpsys thermalservice` lists from Android 10 on: on the SM-A515F, `AP`
# (the CPU), `BAT`, `SKIN` and a `USB` that reads 0.0.
SENSORS = {"battery": None, "cpu": 0, "gpu": 1, "skin": 3}
_HAL_NAMES = {0: "cpu", 1: "gpu", 2: "battery", 3: "skin", 4: "usb_port",
              5: "power_amplifier", 9: "npu"}
# How often a cooling device is asked again. Ten seconds is a fraction of a
# degree on a phone at rest, and an adb call is cheap next to it.
POLL_S = 10
_KEYS = ("max_c", "sensor", "timeout_s")


@dataclass(frozen=True)
class Cooldown:
    max_c: float
    sensor: str = "battery"
    timeout_s: int = 600


def cooldown_of(section: dict[str, Any]) -> Cooldown | None:
    """`runner.cooldown`, or None when it is off: absent, or `false`."""
    value = section.get("cooldown")
    if value is None or value is False:
        return None
    if not isinstance(value, dict):
        raise RunnerError(
            f"runner.cooldown: {value!r} is not a setting. It is a mapping: "
            f"`max_c: 32` under it, the temperature each repeat waits for, and "
            f"`sensor:`, one of {', '.join(SENSORS)} (battery when left out)")
    unknown = [k for k in value if k not in _KEYS]
    if unknown:
        raise RunnerError(
            f"runner.cooldown: no key {', '.join(repr(k) for k in unknown)}. "
            f"It takes {', '.join(_KEYS)}")
    max_c = value.get("max_c")
    if isinstance(max_c, bool) or not isinstance(max_c, (int, float)) or not 0 < max_c < 100:
        raise RunnerError(
            f"runner.cooldown.max_c: {max_c!r} is not a temperature. Degrees "
            f"Celsius in digits: the sensor reading each repeat waits for")
    sensor = value.get("sensor", "battery")
    if sensor not in SENSORS:
        raise RunnerError(
            f"runner.cooldown.sensor: {sensor!r} is not one of "
            f"{', '.join(SENSORS)}")
    timeout = value.get("timeout_s", 600)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 1:
        raise RunnerError(
            f"runner.cooldown.timeout_s: {timeout!r} is not a number of "
            f"seconds. The unit is in the name; the value is digits alone")
    return Cooldown(float(max_c), sensor, int(timeout))


def knobs_of(section: dict[str, Any]) -> dict[str, Any]:
    """The state to hold the device in, from the runner section: only what
    is set, each refused in a sentence when it cannot be meant."""
    knobs: dict[str, Any] = {}
    for key in ("fixed_performance", "airplane_mode"):
        value = section.get(key)
        if value is None or value is False:
            continue
        if value is not True:
            raise RunnerError(f"runner.{key}: {value!r} is not true or false")
        knobs[key] = True
    level = section.get("brightness")
    if level is not None:
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 255:
            raise RunnerError(
                f"runner.brightness: {level!r} is not a screen level. A whole "
                f"number from 0 to 255, as `settings put system "
                f"screen_brightness` takes it")
        knobs["brightness"] = level
    return knobs


def wanted(section: dict[str, Any]) -> bool:
    """Whether the section asks for anything here at all."""
    return cooldown_of(section) is not None or bool(knobs_of(section))


# --- reading the device ------------------------------------------------------

_BATTERY = re.compile(r"^\s*temperature:\s*(-?\d+)\s*$", re.MULTILINE)
_TEMPERATURE = re.compile(
    r"Temperature\{mValue=(?P<value>-?[\d.]+), mType=(?P<type>-?\d+), "
    r"mName=(?P<name>[^,}]*)")
_STATUS = re.compile(r"^Thermal Status:\s*(\d+)", re.MULTILINE)


def battery_c(text: str) -> float | None:
    """The battery's temperature from `dumpsys battery`, which gives tenths
    of a degree: `temperature: 273` is 27.3 °C."""
    m = _BATTERY.search(text)
    return int(m.group(1)) / 10 if m else None


def hal_temperatures(text: str) -> list[tuple[int, str, float]]:
    """(type, name, °C) for each sensor `dumpsys thermalservice` lists under
    "Current temperatures from HAL:", else under "Cached temperatures:".

    A reading of exactly 0.0 is a sensor with nothing to say — the A51's
    `USB` reads it at rest — and is left out.
    """
    for heading in ("Current temperatures from HAL:", "Cached temperatures:"):
        at = text.find(heading)
        if at < 0:
            continue
        block = []
        for line in text[at + len(heading):].splitlines()[1:]:
            if line and not line[0].isspace():
                break
            m = _TEMPERATURE.search(line)
            if m and float(m.group("value")) != 0.0:
                block.append((int(m.group("type")), m.group("name"), float(m.group("value"))))
        if block:
            return block
    return []


def thermal_status(text: str) -> int | None:
    """The platform's throttling level, 0 for none, from `dumpsys
    thermalservice`."""
    m = _STATUS.search(text)
    return int(m.group(1)) if m else None


def cpufreq_limits(run: Callable[..., str], device: str) -> dict[str, tuple[int, int]]:
    """Each cpufreq policy's (min, max) in kHz, as the kernel holds them."""
    out = run(["adb", "-s", device, "shell",
               "for p in /sys/devices/system/cpu/cpufreq/policy*; do "
               "echo $(basename $p) $(cat $p/scaling_min_freq) $(cat $p/scaling_max_freq); "
               "done"])
    limits = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            limits[parts[0]] = (int(parts[1]), int(parts[2]))
    return limits


def _mhz(limits: dict[str, tuple[int, int]]) -> str:
    return ", ".join(f"{p} {lo // 1000}–{hi // 1000} MHz" for p, (lo, hi) in sorted(limits.items()))


# --- around a set --------------------------------------------------------------

class Settled:
    """The device held in the state the section asks for, for one set.

    `with` applies what the section asks and puts it back on the way out;
    `cool()` goes before each repeat. `notify` is told what a wait is waiting
    for, and then that it is over, for the progress file `status` reads: a
    collect that sits two minutes between repeats is cooling, not stuck.
    `sleep` and `clock` are the test's to replace.
    """

    def __init__(self, section: dict[str, Any], device: str, *,
                 run: Callable[..., str], log: Callable[[str], None] = print,
                 notify: Callable[..., None] | None = None,
                 sleep: Callable[[float], None] | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self.cooldown = cooldown_of(section)
        self.knobs = knobs_of(section)
        self.device = device
        self.run = run
        self.log = log
        self.notify = notify or (lambda **kw: None)
        # Looked up here rather than bound as defaults, so that a test that
        # replaces this module's `time` is the one a collect it drives waits with.
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self._undo: list[tuple[str, list[list[str]]]] = []

    def _adb(self, *args: str) -> str:
        return self.run(["adb", "-s", self.device, "shell", *args])

    def read(self) -> float | None:
        """The sensor `cooldown` waits on, in °C, or None when the device
        has no such sensor."""
        sensor = self.cooldown.sensor
        if SENSORS[sensor] is None:
            return battery_c(self._adb("dumpsys", "battery"))
        temps = [t for kind, _, t in hal_temperatures(self._adb("dumpsys", "thermalservice"))
                 if kind == SENSORS[sensor]]
        return max(temps) if temps else None

    def _missing(self) -> RunnerError:
        sensor = self.cooldown.sensor
        if SENSORS[sensor] is None:
            return RunnerError("runner.cooldown: `dumpsys battery` gave no temperature on "
                               f"{self.device}")
        listed = sorted({_HAL_NAMES.get(k, f"type {k}")
                         for k, _, _ in hal_temperatures(self._adb("dumpsys", "thermalservice"))})
        has = (f"it reports {', '.join(listed)}" if listed
               else "it reports no temperatures at all")
        return RunnerError(
            f"runner.cooldown.sensor: {self.device}'s thermal HAL has no `{sensor}` "
            f"sensor: {has}. `battery` reads from `dumpsys battery`, which "
            f"every device has")

    def __enter__(self) -> Settled:
        if self.cooldown is not None:
            # Read before anything is changed: a sensor that is not there
            # stops the set with the device as it was.
            now = self.read()
            if now is None:
                raise self._missing()
            c = self.cooldown
            self.log(f"  cooldown: {c.sensor} at or below {c.max_c:g} °C before each "
                     f"repeat, up to {c.timeout_s}s; now {now:.1f} °C")
        steps: list[tuple[str, Callable[[], None]]] = []
        if self.knobs.get("fixed_performance"):
            steps.append(("fixed performance mode", self._fixed_performance))
        if self.knobs.get("airplane_mode"):
            steps.append(("airplane mode", self._airplane_mode))
        if "brightness" in self.knobs:
            steps.append(("screen brightness", lambda: self._brightness(self.knobs["brightness"])))
        try:
            for what, step in steps:
                # One knob the device will not read or take is that knob's
                # problem: an Android before 11 has no `cmd connectivity
                # airplane-mode`, and the screen can still be held.
                try:
                    step()
                except RunnerError as e:
                    self.log(f"  [!] {what}: {e.gist or e}; the set runs without it")
        except BaseException:
            # Out of `__enter__` the `with` never reaches `__exit__`, so what
            # was changed before an interrupt is put back here.
            self.__exit__(None, None, None)
            raise
        return self

    def _change(self, what: str, apply: list[list[str]], undo: list[list[str]]) -> bool:
        """Run `apply`; on success remember `undo` for the way out."""
        try:
            for args in apply:
                self._adb(*args)
        except RunnerError as e:
            self.log(f"  [!] {what}: the device refused it ({e.gist or e}); the set "
                     f"runs without it")
            return False
        self._undo.append((what, undo))
        return True

    def _fixed_performance(self) -> None:
        # The mode cannot be read back, so what it did is read off the
        # cpufreq limits instead. On the SM-A515F the command succeeds and
        # moves none of them: its power HAL has no such mode, and saying
        # "on" there would be saying something that is not so.
        before = cpufreq_limits(self.run, self.device)
        if not self._change("fixed performance mode",
                            [["cmd", "power", "set-fixed-performance-mode-enabled", "true"]],
                            [["cmd", "power", "set-fixed-performance-mode-enabled", "false"]]):
            return
        after = cpufreq_limits(self.run, self.device)
        if before and after == before:
            self.log(f"  [!] fixed performance mode: the device took it and kept every "
                     f"frequency limit as it was ({_mhz(after)}): its power HAL does not "
                     f"implement the mode, so the clock still follows the load")
        else:
            self.log(f"  fixed performance mode on: {_mhz(after) or 'limits not readable'}")

    def _airplane_mode(self) -> None:
        state = self._adb("cmd", "connectivity", "airplane-mode").strip()
        if state == "enabled":
            self.log("  airplane mode: already on, and left as it is")
            return
        if self._change("airplane mode", [["cmd", "connectivity", "airplane-mode", "enable"]],
                        [["cmd", "connectivity", "airplane-mode", "disable"]]):
            self.log("  airplane mode on")

    def _brightness(self, level: int) -> None:
        mode = self._adb("settings", "get", "system", "screen_brightness_mode").strip()
        value = self._adb("settings", "get", "system", "screen_brightness").strip()
        undo = [["settings", "put", "system", "screen_brightness", value]] if value.isdigit() else []
        if mode in ("0", "1"):
            undo.append(["settings", "put", "system", "screen_brightness_mode", mode])
        if self._change("screen brightness",
                        [["settings", "put", "system", "screen_brightness_mode", "0"],
                         ["settings", "put", "system", "screen_brightness", str(level)]],
                        undo):
            self.log(f"  screen brightness {level}, auto-brightness off (was "
                     f"{value}{', auto' if mode == '1' else ''})")

    def __exit__(self, *exc) -> bool:
        for what, undo in reversed(self._undo):
            try:
                for args in undo:
                    self._adb(*args)
                self.log(f"  put back: {what}")
            except RunnerError as e:
                self.log(f"  [!] could not put back {what}: {e.gist or e}")
        self._undo.clear()
        return False

    def cool(self) -> float:
        """Wait until the sensor reads at or below `max_c`, and return how
        long that took. A device that does not get there in `timeout_s` is
        recorded as it is, and the line says so: the report then shows how
        hot it ran, and a set is not lost to a warm room."""
        if self.cooldown is None:
            return 0.0
        c = self.cooldown
        now = self.read()
        if now is None or now <= c.max_c:
            return 0.0
        self.log(f"  cooling: {c.sensor} {now:.1f} °C, waiting for {c.max_c:g}")
        started = self.clock()
        try:
            while now is not None and now > c.max_c:
                self.notify(cooling=f"{c.sensor} {now:.1f} °C, waiting for {c.max_c:g}")
                waited = self.clock() - started
                if waited >= c.timeout_s:
                    self.log(f"  [!] still {now:.1f} °C after {waited:.0f}s; recording "
                             f"anyway, and the report says how hot it ran")
                    return waited
                self.sleep(min(POLL_S, c.timeout_s - waited))
                now = self.read()
        finally:
            self.notify(cooling=None)
        waited = self.clock() - started
        self.log(f"  cooled to {now:.1f} °C in {waited:.0f}s")
        return waited
